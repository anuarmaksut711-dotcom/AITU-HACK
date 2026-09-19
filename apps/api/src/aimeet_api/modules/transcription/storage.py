import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, Request

from aimeet_api.core.config import Settings


def audio_path(settings: Settings, meeting_id: uuid.UUID) -> Path:
    # No client-supplied path or filename is used for storage.
    return settings.audio_dir / f"{meeting_id}.audio"


async def store_audio(request: Request, settings: Settings, meeting_id: uuid.UUID):
    target = audio_path(settings, meeting_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(".partial")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_audio_bytes:
                    raise HTTPException(413, "Audio file is too large")
                digest.update(chunk)
                stream.write(chunk)
            if size == 0:
                raise HTTPException(422, "Audio file is empty")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.rename(target)
        return size, digest.hexdigest()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
