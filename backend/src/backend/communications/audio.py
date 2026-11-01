"""Bounded media inspection; no user filenames, temporary files or network protocols."""

import asyncio
import json
from decimal import Decimal, InvalidOperation

from backend.core.errors import ApiError


async def validate_audio(content: bytes, mime: str, duration_ms: int) -> None:
    formats = {"audio/mp4": "mov", "audio/webm": "matroska", "audio/ogg": "ogg"}
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-f",
            formats[mime],
            "-i",
            "pipe:0",
            "-show_entries",
            "stream=codec_type,codec_name:format=duration",
            "-of",
            "json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            503, "audio_validation_unavailable", "Audio validation is unavailable"
        ) from exc
    try:
        output, _ = await asyncio.wait_for(process.communicate(content), timeout=10)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ApiError(422, "invalid_voice", "Recording could not be inspected") from exc
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    try:
        data = json.loads(output)
        streams = data.get("streams", [])
        if (
            process.returncode
            or not streams
            or any(
                stream.get("codec_type") != "audio"
                or stream.get("codec_name") not in {"aac", "opus", "vorbis"}
                for stream in streams
            )
        ):
            raise ValueError("not_audio")
        duration = data.get("format", {}).get("duration")
        if duration is not None:
            actual = Decimal(duration) * 1000
            if (
                not actual.is_finite()
                or actual < 300
                or actual > 301000
                or abs(actual - duration_ms) > 2000
            ):
                raise ValueError("invalid_duration")
    except (ValueError, KeyError, InvalidOperation) as exc:
        raise ApiError(422, "invalid_voice", "Choose a valid audio recording") from exc
