"""Firebase transport: generic alerts only, never private notification bodies."""

from datetime import timedelta
from uuid import UUID

import firebase_admin
from firebase_admin import credentials, exceptions, messaging

from backend.communications.dispatch import InvalidPushToken, PermanentPushError
from backend.core.config import Settings


class FirebasePushProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.firebase_project_id:
            raise ValueError("M1917_FIREBASE_PROJECT_ID is required for push")
        credential = (
            credentials.Certificate(settings.firebase_credentials_file)
            if settings.firebase_credentials_file
            else credentials.ApplicationDefault()
        )
        self.app = firebase_admin.initialize_app(
            credential,
            {"projectId": settings.firebase_project_id, "httpTimeout": 8},
            name="alash-push-worker",
        )
        self.web_url = settings.push_web_url

    async def send(self, *, token: str, notification_id: UUID, idempotency_key: str) -> None:
        identity = str(notification_id)
        message = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title="ALASH M-1917", body="Новое уведомление. Откройте приложение."
            ),
            data={"notification_id": identity},
            android=messaging.AndroidConfig(
                ttl=timedelta(minutes=5),
                collapse_key=identity,
                notification=messaging.AndroidNotification(tag=identity, sound="default"),
            ),
            apns=messaging.APNSConfig(
                headers={"apns-collapse-id": identity},
                payload=messaging.APNSPayload(aps=messaging.Aps(sound="default")),
            ),
            webpush=messaging.WebpushConfig(
                headers={"TTL": "300"},
                notification=messaging.WebpushNotification(tag=identity),
                fcm_options=messaging.WebpushFCMOptions(link=self.web_url)
                if self.web_url
                else None,
            ),
        )
        result = await messaging.send_each_async([message], app=self.app)
        error = result.responses[0].exception
        if error is None:
            return
        if isinstance(error, messaging.UnregisteredError):
            raise InvalidPushToken from None
        if isinstance(
            error,
            (
                messaging.SenderIdMismatchError,
                exceptions.InvalidArgumentError,
            ),
        ):
            raise PermanentPushError from None
        # Do not retain SDK exception messages, response bodies or device tokens.
        raise RuntimeError("push_provider_unavailable") from None

    def close(self) -> None:
        firebase_admin.delete_app(self.app)
