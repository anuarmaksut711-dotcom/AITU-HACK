import hashlib
import hmac
import time
import uuid

import httpx
import jwt
from fastapi import HTTPException

from aimeet_api.core.config import Settings
from aimeet_api.modules.live.models import LiveParticipant, LiveRoom
from aimeet_api.modules.live.schemas import JoinGrant


def secret(settings: Settings) -> str:
    value = settings.livekit_api_secret.get_secret_value()
    if len(value) < 32:
        raise HTTPException(503, "Live rooms are not configured")
    return value


def room_name(room_id: uuid.UUID) -> str:
    return f"soyle-{room_id}"


def invite_token(settings: Settings, room: LiveRoom) -> str:
    return hmac.new(
        secret(settings).encode(),
        f"soyle-invite:{room.id}:{room.invite_nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()


def validate_invite(settings: Settings, room: LiveRoom | None, value: str) -> LiveRoom:
    if room is None or not hmac.compare_digest(invite_token(settings, room), value):
        raise HTTPException(404, "Invitation not found")
    return room


def media_jwt(settings: Settings, participant: LiveParticipant) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": settings.livekit_api_key,
            "sub": str(participant.id),
            "name": participant.name,
            "nbf": now - 5,
            "exp": now + 300,
            "video": {
                "roomJoin": True,
                "room": room_name(participant.room_id),
                "canPublish": True,
                "canPublishSources": ["microphone"],
                "canSubscribe": True,
                # The SDK opens transport/control data channels even for a voice-only room.
                # Application state and insights never trust participant data-channel payloads.
                "canPublishData": True,
                "canUpdateOwnMetadata": False,
            },
        },
        secret(settings),
        algorithm="HS256",
    )


def member_jwt(settings: Settings, participant: LiveParticipant) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "soyle-live",
            "aud": "soyle-live-app",
            "sub": str(participant.id),
            "room": str(participant.room_id),
            "iat": now,
            "exp": now + 86400,
        },
        secret(settings),
        algorithm="HS256",
    )


def decode_member(settings: Settings, token: str, room_id: uuid.UUID) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            secret(settings),
            algorithms=["HS256"],
            audience="soyle-live-app",
            issuer="soyle-live",
            options={"require": ["sub", "room", "exp"]},
        )
        if claims["room"] != str(room_id):
            raise ValueError("Wrong room")
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(401, "Room access expired") from exc


def grant(settings: Settings, participant: LiveParticipant, *, can_join=True) -> JoinGrant:
    return JoinGrant(
        room_id=participant.room_id,
        participant_id=participant.id,
        name=participant.name,
        is_host=participant.is_host,
        member_token=member_jwt(settings, participant),
        media_token=media_jwt(settings, participant) if can_join else "",
        media_url=settings.livekit_public_url,
    )


def media_room_request(settings: Settings, action: str, room_id: uuid.UUID) -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": settings.livekit_api_key,
            "nbf": now - 5,
            "exp": now + 60,
            "video": {"roomCreate": True, "roomAdmin": True, "room": room_name(room_id)},
        },
        secret(settings),
        algorithm="HS256",
    )
    payload = (
        {
            "name": room_name(room_id),
            "empty_timeout": 600,
            "max_participants": settings.live_max_participants,
        }
        if action == "CreateRoom"
        else {"room": room_name(room_id)}
    )
    try:
        with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                settings.livekit_api_url.rstrip("/") + f"/twirp/livekit.RoomService/{action}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if action == "DeleteRoom" and response.status_code == 404:
                return
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Voice service is unavailable") from exc
