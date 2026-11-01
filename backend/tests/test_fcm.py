from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from firebase_admin import exceptions, messaging

from backend.communications.dispatch import InvalidPushToken, PermanentPushError
from backend.communications.fcm import FirebasePushProvider


@pytest.mark.parametrize(
    "error,expected",
    [
        (None, None),
        (messaging.UnregisteredError("secret"), InvalidPushToken),
        (messaging.SenderIdMismatchError("secret"), PermanentPushError),
        (exceptions.InvalidArgumentError("secret"), PermanentPushError),
        (exceptions.UnavailableError("secret"), RuntimeError),
    ],
)
async def test_transport_payload_and_error_redaction(monkeypatch, error, expected):
    provider = object.__new__(FirebasePushProvider)
    provider.app = object()
    provider.web_url = "https://app.example.org"
    sender = AsyncMock(return_value=SimpleNamespace(responses=[SimpleNamespace(exception=error)]))
    monkeypatch.setattr(messaging, "send_each_async", sender)
    identity = uuid4()
    if expected:
        with pytest.raises(expected) as raised:
            await provider.send(
                token="private-token", notification_id=identity, idempotency_key="key"
            )
        assert "secret" not in str(raised.value)
    else:
        await provider.send(token="private-token", notification_id=identity, idempotency_key="key")
    message = sender.call_args.args[0][0]
    assert message.token == "private-token"
    assert message.data == {"notification_id": str(identity)}
    assert message.notification.body == "Новое уведомление. Откройте приложение."
    assert message.android.notification.tag == str(identity)
    assert message.apns.headers["apns-collapse-id"] == str(identity)
    assert message.webpush.fcm_options.link == provider.web_url
