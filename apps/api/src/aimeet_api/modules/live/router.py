import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select, update

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, SettingsDep, require_csrf
from aimeet_api.core.security import hash_token
from aimeet_api.db.models import AuthSession, User, utcnow
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRoom
from aimeet_api.modules.live.schemas import (
    GuestJoin,
    InviteCheck,
    JoinGrant,
    ParticipantOutput,
    RoomCreate,
    RoomState,
    RoomSummary,
    UtteranceOutput,
)
from aimeet_api.modules.live.security import (
    decode_member,
    grant,
    invite_token,
    media_room_request,
    secret,
    validate_invite,
)

router = APIRouter(prefix="/live", tags=["Live meetings"])


def check_host_session(db, member, session_token):
    if not member.is_host:
        return
    valid = db.scalar(
        select(AuthSession.id)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.user_id == member.user_id,
            AuthSession.token_hash == hash_token(session_token or ""),
            AuthSession.expires_at > utcnow(),
            User.is_active.is_(True),
        )
    )
    if not valid:
        raise HTTPException(401, "Host account session expired")


def participant_for(db, settings, room_id, authorization, session_token=None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Room access required")
    identifier = decode_member(settings, authorization[7:], room_id)
    member = db.get(LiveParticipant, identifier)
    room = db.get(LiveRoom, room_id)
    if member is None or member.room_id != room_id or member.revoked or room is None:
        raise HTTPException(404, "Room not found")
    check_host_session(db, member, session_token)
    return room, member


Authorization = Annotated[str | None, Header()]


@router.get("/rooms", response_model=list[RoomSummary], operation_id="listLiveRooms")
def list_rooms(user: CurrentUser, db: DatabaseDep):
    return db.scalars(
        select(LiveRoom)
        .where(LiveRoom.workspace_id == user.workspace_id, LiveRoom.created_by == user.id)
        .order_by(LiveRoom.created_at.desc())
        .limit(50)
    ).all()


@router.post(
    "/rooms",
    response_model=JoinGrant,
    status_code=201,
    dependencies=[Depends(require_csrf)],
    operation_id="createLiveRoom",
)
def create_room(payload: RoomCreate, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    secret(settings)
    active = db.scalar(
        select(func.count())
        .select_from(LiveRoom)
        .where(LiveRoom.created_by == user.id, LiveRoom.status == "active")
    )
    if active >= 3:
        raise HTTPException(409, "End an existing room before creating another")
    room = LiveRoom(
        id=uuid.uuid4(),
        workspace_id=user.workspace_id,
        created_by=user.id,
        title=payload.title,
        language=payload.language,
    )
    # Failure to provision media must not leave a successful-looking room.
    media_room_request(settings, "CreateRoom", room.id)
    try:
        db.add(room)
        db.flush()
        member = LiveParticipant(
            room_id=room.id, user_id=user.id, name=user.display_name[:80], is_host=True
        )
        db.add(member)
        db.commit()
    except Exception:
        db.rollback()
        media_room_request(settings, "DeleteRoom", room.id)
        raise
    return grant(settings, member, can_join=room.status == "active")


@router.post(
    "/rooms/{room_id}/host",
    response_model=JoinGrant,
    dependencies=[Depends(require_csrf)],
    operation_id="rejoinHostedRoom",
)
def host_join(room_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    room = db.scalar(
        select(LiveRoom).where(
            LiveRoom.id == room_id,
            LiveRoom.workspace_id == user.workspace_id,
            LiveRoom.created_by == user.id,
        )
    )
    if room is None:
        raise HTTPException(404, "Room not found")
    member = db.scalar(
        select(LiveParticipant).where(
            LiveParticipant.room_id == room_id,
            LiveParticipant.user_id == user.id,
            LiveParticipant.is_host.is_(True),
        )
    )
    if member is None:
        raise HTTPException(404, "Room not found")
    return grant(settings, member, can_join=room.status == "active")


@router.post(
    "/rooms/{room_id}/invitation",
    dependencies=[Depends(require_csrf)],
    operation_id="checkLiveInvitation",
    response_model=RoomSummary,
)
def check_invitation(
    room_id: uuid.UUID, payload: InviteCheck, db: DatabaseDep, settings: SettingsDep
):
    return validate_invite(settings, db.get(LiveRoom, room_id), payload.invite)


@router.post(
    "/rooms/{room_id}/join",
    response_model=JoinGrant,
    dependencies=[Depends(require_csrf)],
    operation_id="joinLiveRoom",
)
def join_room(room_id: uuid.UUID, payload: GuestJoin, db: DatabaseDep, settings: SettingsDep):
    room = validate_invite(
        settings,
        db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update()),
        payload.invite,
    )
    if room.status != "active":
        raise HTTPException(409, "This meeting has ended")
    count = db.scalar(
        select(func.count())
        .select_from(LiveParticipant)
        .where(LiveParticipant.room_id == room_id, LiveParticipant.revoked.is_(False))
    )
    if count >= settings.live_max_participants:
        raise HTTPException(409, "The room participant limit has been reached")
    member = LiveParticipant(room_id=room_id, name=payload.name)
    db.add(member)
    db.commit()
    return grant(settings, member)


@router.get("/rooms/{room_id}", response_model=RoomState, operation_id="getLiveRoom")
def get_room(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
):
    room, member = participant_for(
        db, settings, room_id, authorization, request.cookies.get(settings.cookie_name)
    )
    member.last_seen_at = utcnow()
    db.commit()
    people = db.scalars(select(LiveParticipant).where(LiveParticipant.room_id == room_id)).all()
    names = {person.id: person.name for person in people}
    # Keep polling bounded; the complete transcript is available through the paginated endpoint.
    chunks = list(
        reversed(
            db.scalars(
                select(LiveChunk)
                .where(
                    LiveChunk.room_id == room_id, LiveChunk.status == "done", LiveChunk.text != ""
                )
                .order_by(LiveChunk.start.desc(), LiveChunk.id.desc())
                .limit(300)
            ).all()
        )
    )
    return RoomState(
        **RoomSummary.model_validate(room).model_dump(),
        participants=[ParticipantOutput.model_validate(p) for p in people if not p.revoked],
        utterances=[
            UtteranceOutput(
                id=c.id,
                participant_id=c.participant_id,
                speaker=names.get(c.participant_id, "Участник"),
                start=c.start,
                end=c.end,
                text=c.text,
            )
            for c in chunks
        ],
        insights=room.insights,
        transcript_revision=room.transcript_revision,
        analysis_status=room.analysis_status,
        analysis_error=room.analysis_error,
        audio_error=room.audio_error,
        analysis_provider=settings.rag_llm_provider,
        can_end=member.is_host,
    )


@router.get(
    "/rooms/{room_id}/transcript",
    response_model=list[UtteranceOutput],
    operation_id="getLiveTranscript",
)
def transcript(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
    offset: int = 0,
):
    participant_for(db, settings, room_id, authorization, request.cookies.get(settings.cookie_name))
    if not 0 <= offset <= 20000:
        raise HTTPException(422, "Invalid offset")
    rows = db.execute(
        select(LiveChunk, LiveParticipant.name)
        .join(LiveParticipant)
        .where(LiveChunk.room_id == room_id, LiveChunk.status == "done", LiveChunk.text != "")
        .order_by(LiveChunk.start, LiveChunk.id)
        .offset(offset)
        .limit(300)
    ).all()
    return [
        UtteranceOutput(
            id=c.id,
            participant_id=c.participant_id,
            speaker=name,
            start=c.start,
            end=c.end,
            text=c.text,
        )
        for c, name in rows
    ]


@router.post(
    "/rooms/{room_id}/token",
    response_model=JoinGrant,
    dependencies=[Depends(require_csrf)],
    operation_id="refreshLiveGrant",
)
def refresh_grant(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
):
    room, member = participant_for(
        db, settings, room_id, authorization, request.cookies.get(settings.cookie_name)
    )
    if room.status != "active":
        raise HTTPException(409, "This meeting has ended")
    # Explicit provisioning restores an idle room without allowing old JWTs to recreate ended rooms.
    media_room_request(settings, "CreateRoom", room_id)
    return grant(settings, member)


@router.get("/rooms/{room_id}/invite", operation_id="getLiveInvite")
def get_invite(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
) -> dict[str, str]:
    room, member = participant_for(
        db, settings, room_id, authorization, request.cookies.get(settings.cookie_name)
    )
    if not member.is_host:
        raise HTTPException(403, "Only the host can invite")
    if room.status != "active":
        raise HTTPException(409, "This meeting has ended")
    return {"invite": invite_token(settings, room)}


@router.post(
    "/rooms/{room_id}/end", dependencies=[Depends(require_csrf)], operation_id="endLiveRoom"
)
def end_room(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
) -> dict[str, str]:
    room, member = participant_for(
        db, settings, room_id, authorization, request.cookies.get(settings.cookie_name)
    )
    if not member.is_host:
        raise HTTPException(403, "Only the host can end the meeting")
    if room.status == "active":
        media_room_request(settings, "DeleteRoom", room_id)
        db.execute(
            update(LiveRoom)
            .where(LiveRoom.id == room_id, LiveRoom.status == "active")
            .values(status="ending", ended_at=utcnow())
        )
        db.commit()
    return {"status": "ending" if room.status != "ended" else "ended"}


@router.post(
    "/rooms/{room_id}/analysis/retry",
    dependencies=[Depends(require_csrf)],
    operation_id="retryLiveAnalysis",
)
def retry_analysis(
    room_id: uuid.UUID,
    db: DatabaseDep,
    settings: SettingsDep,
    request: Request,
    authorization: Authorization = None,
) -> dict[str, str]:
    room, member = participant_for(
        db, settings, room_id, authorization, request.cookies.get(settings.cookie_name)
    )
    if not member.is_host:
        raise HTTPException(403, "Only the host can retry analysis")
    if room.analysis_status != "failed":
        raise HTTPException(409, "Analysis is not failed")
    room.analysis_status = "waiting"
    room.analysis_error = None
    room.analysis_through = max(0, room.transcript_revision - 1)
    room.analysis_updated_at = utcnow() - timedelta(seconds=60)
    db.commit()
    return {"status": "waiting"}
