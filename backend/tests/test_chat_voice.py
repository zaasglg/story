from pathlib import Path

import pytest
from conftest import login
from sqlalchemy import func, select

from backend.communications.models import ChatMessage, Notification
from backend.identity.models import User, UserRole

FIXTURES = Path(__file__).parent / "fixtures"


async def voice_context(context, sender):
    headers = {}
    for role in UserRole:
        tokens = await login(context, role)
        headers[role] = {"Authorization": f"Bearer {tokens['access_token']}"}
    peer = UserRole.MASTER if sender == UserRole.WORKER else UserRole.WORKER
    async with context.database.session_factory() as session:
        peer_id = await session.scalar(select(User.id).where(User.role == peer))
    thread = await context.client.post(f"/api/v1/chats/direct/{peer_id}", headers=headers[sender])
    assert thread.status_code == 200
    return headers, peer, thread.json()["id"]


@pytest.mark.parametrize("sender", list(UserRole))
@pytest.mark.parametrize(
    "filename,mime",
    [("voice.m4a", "audio/mp4"), ("voice.webm", "audio/webm"), ("voice.ogg", "audio/ogg")],
)
async def test_voice_delivery_privacy_and_retry(context, sender, filename, mime):
    headers, peer, thread = await voice_context(context, sender)
    content = (FIXTURES / filename).read_bytes()
    endpoint = f"/api/v1/chats/{thread}/voice"
    payload = {"client_message_id": "voice-one", "duration_ms": "1000"}

    async def send(data=payload, audio=content):
        return await context.client.post(
            endpoint, headers=headers[sender], data=data, files={"file": (filename, audio, mime)}
        )

    sent = await send()
    assert sent.status_code == 201, sent.text
    message = sent.json()
    assert message["voice_mime"] == mime and message["voice_duration_ms"] == 1000
    assert message["is_own"] is True and message["body"] == "Голосовое сообщение"
    assert "voice_content" not in message and "voice_sha256" not in message
    replay = await send()
    assert replay.json()["id"] == message["id"]
    assert (await send({**payload, "duration_ms": "1500"})).status_code == 409
    assert (await send(audio=content + b"different")).status_code == 409
    audio_path = "/api/v1" + message["voice_url"]
    for role in UserRole:
        audio = await context.client.get(audio_path, headers=headers[role])
        assert audio.status_code == (200 if role in (sender, peer) else 404)
        if audio.status_code == 200:
            assert audio.content == content
            assert audio.headers["content-type"] == mime
            assert audio.headers["cache-control"] == "private, no-store"
    assert (await context.client.get(audio_path)).status_code == 401
    history = await context.client.get(f"/api/v1/chats/{thread}/messages", headers=headers[peer])
    assert history.json()[0]["voice_url"] == message["voice_url"]
    assert history.json()[0]["is_own"] is False
    text = await context.client.post(
        f"/api/v1/chats/{thread}/messages",
        headers=headers[sender],
        json={"client_message_id": "voice-one", "body": "Голосовое сообщение"},
    )
    assert text.status_code == 409
    outsider = next(role for role in UserRole if role not in (sender, peer))
    denied = await context.client.post(
        endpoint, headers=headers[outsider], data=payload, files={"file": (filename, content, mime)}
    )
    assert denied.status_code == 404
    async with context.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ChatMessage)) == 1
        assert await session.scalar(select(func.count()).select_from(Notification)) == 1


async def test_invalid_voice_and_text_collision(context):
    headers, _, thread = await voice_context(context, UserRole.WORKER)
    endpoint = f"/api/v1/chats/{thread}/voice"
    for content, duration, expected in (
        (b"", "1000", 422),
        (b"<script>" * 10, "1000", 422),
        (b"x" * (5 * 1024 * 1024 + 1), "1000", 413),
        ((FIXTURES / "voice.m4a").read_bytes(), "100", 422),
        ((FIXTURES / "voice.m4a").read_bytes(), "300001", 422),
    ):
        response = await context.client.post(
            endpoint,
            headers=headers[UserRole.WORKER],
            data={"client_message_id": "invalid", "duration_ms": duration},
            files={"file": ("voice.m4a", content, "audio/mp4")},
        )
        assert response.status_code == expected, response.text
    sent = await context.client.post(
        f"/api/v1/chats/{thread}/messages",
        headers=headers[UserRole.WORKER],
        json={"client_message_id": "text-one", "body": "Original text"},
    )
    assert sent.status_code == 201
    collision = await context.client.post(
        endpoint,
        headers=headers[UserRole.WORKER],
        data={"client_message_id": "text-one", "duration_ms": "1000"},
        files={"file": ("voice.m4a", (FIXTURES / "voice.m4a").read_bytes(), "audio/mp4")},
    )
    assert collision.status_code == 409


async def test_magic_header_does_not_make_executable_or_video_a_valid_voice(context):
    headers, _, thread = await voice_context(context, UserRole.WORKER)
    for fake in (
        b"\x00\x00\x00\x18ftyp" + b"<script>evil</script>" * 10,
        b"OggS" + b"not-an-audio-stream" * 10,
        b"\x1a\x45\xdf\xa3" + b"not-a-webm-audio" * 10,
    ):
        response = await context.client.post(
            f"/api/v1/chats/{thread}/voice",
            headers=headers[UserRole.WORKER],
            data={"client_message_id": "invalid-container", "duration_ms": "1000"},
            files={"file": ("voice.m4a", fake, "audio/mp4")},
        )
        assert response.status_code == 422, response.text
