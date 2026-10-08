from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.service import add_audit_event
from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.communications.models import (
    Announcement,
    ChatMessage,
    ChatThread,
    Notification,
    SupportTicket,
)
from backend.communications.push import stage_notifications
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.identity.dependencies import CurrentUser, get_db_session
from backend.identity.models import AccountStatus, EmployeeProfile, User

router = APIRouter(tags=["communications"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
Publisher = Annotated[CurrentUser, Depends(require_permission(Permission.ANNOUNCEMENT_CREATE))]
Receiver = Annotated[CurrentUser, Depends(require_permission(Permission.ANNOUNCEMENT_RECEIVE))]
Chatter = Annotated[CurrentUser, Depends(require_permission(Permission.CHAT_USE))]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class TextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)


class AnnouncementRead(TextCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class NotificationRead(AnnouncementRead):
    read_at: datetime | None


@router.post("/announcements", response_model=AnnouncementRead, status_code=201)
async def announce(payload: TextCreate, session: Session, actor: Publisher) -> Announcement:
    item = Announcement(**payload.model_dump(), created_by=actor.id, created_at=utc_now())
    session.add(item)
    await session.flush()
    recipients = (
        await session.scalars(
            select(User.id).where(User.status == AccountStatus.ACTIVE, User.archived_at.is_(None))
        )
    ).all()
    await stage_notifications(
        session,
        [
            Notification(
                recipient_user_id=user_id,
                title=payload.title,
                body=payload.body,
                announcement_id=item.id,
                created_at=item.created_at,
            )
            for user_id in recipients
        ],
    )
    add_audit_event(
        session,
        actor_user_id=actor.id,
        action="ANNOUNCEMENT_CREATED",
        entity_type="announcement",
        entity_id=item.id,
        new_value=payload.model_dump(),
    )
    await session.commit()
    return item


@router.get("/announcements", response_model=list[AnnouncementRead])
async def announcements(
    session: Session, _: Receiver, limit: Limit = 50, offset: Offset = 0
) -> list[Announcement]:
    return list(
        (
            await session.scalars(
                select(Announcement)
                .order_by(Announcement.created_at.desc(), Announcement.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.get("/me/notifications", response_model=list[NotificationRead])
async def inbox(
    session: Session, actor: Receiver, limit: Limit = 50, offset: Offset = 0
) -> list[Notification]:
    return list(
        (
            await session.scalars(
                select(Notification)
                .where(Notification.recipient_user_id == actor.id)
                .order_by(Notification.created_at.desc(), Notification.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.post("/me/notifications/{item_id}/read", response_model=NotificationRead)
async def read_notification(item_id: UUID, session: Session, actor: Receiver) -> Notification:
    item = await session.scalar(
        select(Notification).where(
            Notification.id == item_id, Notification.recipient_user_id == actor.id
        )
    )
    if item is None:
        raise ApiError(404, "notification_not_found", "Notification was not found")
    if item.read_at is None:
        item.read_at = utc_now()
        await session.commit()
    return item


class ThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    key: str
    user_one_id: UUID | None
    user_two_id: UUID | None


async def require_thread(session: AsyncSession, thread_id: UUID, actor: CurrentUser) -> ChatThread:
    item = await session.get(ChatThread, thread_id)
    if item is None or (
        item.key != "GENERAL" and actor.id not in (item.user_one_id, item.user_two_id)
    ):
        raise ApiError(404, "chat_not_found", "Chat was not found")
    return item


@router.get("/chats", response_model=list[ThreadRead])
async def chats(session: Session, actor: Chatter) -> list[ChatThread]:
    return list(
        (
            await session.scalars(
                select(ChatThread)
                .where(
                    or_(
                        ChatThread.key == "GENERAL",
                        ChatThread.user_one_id == actor.id,
                        ChatThread.user_two_id == actor.id,
                    )
                )
                .order_by(ChatThread.created_at)
            )
        ).all()
    )


@router.post("/chats/direct/{recipient_id}", response_model=ThreadRead)
async def direct_chat(recipient_id: UUID, session: Session, actor: Chatter) -> ChatThread:
    if recipient_id == actor.id:
        raise ApiError(422, "invalid_chat_recipient", "Choose another chat participant")
    recipient = await session.get(User, recipient_id)
    if (
        recipient is None
        or recipient.status != AccountStatus.ACTIVE
        or recipient.archived_at is not None
    ):
        raise ApiError(404, "recipient_not_found", "Chat recipient was not found")
    one, two = sorted((actor.id, recipient_id), key=str)
    key = f"{one}:{two}"
    item = await session.scalar(select(ChatThread).where(ChatThread.key == key))
    if item:
        return item
    item = ChatThread(key=key, user_one_id=one, user_two_id=two, created_at=utc_now())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        item = await session.scalar(select(ChatThread).where(ChatThread.key == key))
        if item is None:
            raise ApiError(409, "chat_conflict", "Chat could not be created") from None
    return item


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=5000)


@router.post("/chats/manager", response_model=ThreadRead)
async def manager_chat(session: Session, actor: Chatter) -> ChatThread:
    manager_id = await session.scalar(
        select(EmployeeProfile.master_user_id).where(
            EmployeeProfile.user_id == actor.id, EmployeeProfile.archived_at.is_(None)
        )
    )
    if manager_id is None:
        raise ApiError(404, "manager_not_assigned", "No manager is assigned")
    return await direct_chat(manager_id, session, actor)


class MessageRead(MessageCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    thread_id: UUID
    sender_user_id: UUID
    created_at: datetime


@router.get("/chats/{thread_id}/messages", response_model=list[MessageRead])
async def messages(
    thread_id: UUID,
    session: Session,
    actor: Chatter,
    limit: Limit = 50,
    offset: Offset = 0,
    before_id: UUID | None = None,
) -> list[ChatMessage]:
    await require_thread(session, thread_id, actor)
    query = select(ChatMessage).where(ChatMessage.thread_id == thread_id)
    if before_id is not None:
        anchor = await session.get(ChatMessage, before_id)
        if anchor is None or anchor.thread_id != thread_id:
            raise ApiError(404, "message_not_found", "Message was not found")
        query = query.where(
            or_(
                ChatMessage.created_at < anchor.created_at,
                (ChatMessage.created_at == anchor.created_at) & (ChatMessage.id > anchor.id),
            )
        )
    return list(
        (
            await session.scalars(
                query.order_by(ChatMessage.created_at.desc(), ChatMessage.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.post("/chats/{thread_id}/messages", response_model=MessageRead, status_code=201)
async def send_message(
    thread_id: UUID, payload: MessageCreate, session: Session, actor: Chatter
) -> ChatMessage:
    thread = await require_thread(session, thread_id, actor)
    query = select(ChatMessage).where(
        ChatMessage.thread_id == thread_id,
        ChatMessage.sender_user_id == actor.id,
        ChatMessage.client_message_id == payload.client_message_id,
    )
    existing = await session.scalar(query)
    if existing:
        if existing.body != payload.body:
            raise ApiError(409, "message_key_conflict", "Message key was already used")
        return existing
    item = ChatMessage(
        **payload.model_dump(), thread_id=thread_id, sender_user_id=actor.id, created_at=utc_now()
    )
    session.add(item)
    try:
        if thread.key != "GENERAL":
            recipient_id = (
                thread.user_two_id if thread.user_one_id == actor.id else thread.user_one_id
            )
            await stage_notifications(
                session,
                [
                    Notification(
                        recipient_user_id=recipient_id,
                        title="Жаңа хабарлама",
                        body=payload.body,
                        created_at=item.created_at,
                    )
                ],
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing = await session.scalar(query)
        if existing and existing.body == payload.body:
            return existing
        raise ApiError(409, "message_key_conflict", "Message key was already used") from exc
    return item


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class TicketRead(TicketCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    created_at: datetime
    resolved_at: datetime | None


@router.post("/me/support-tickets", response_model=TicketRead, status_code=201)
async def create_ticket(payload: TicketCreate, session: Session, actor: Chatter) -> SupportTicket:
    query = select(SupportTicket).where(
        SupportTicket.requester_user_id == actor.id,
        SupportTicket.client_request_id == payload.client_request_id,
    )
    if payload.client_request_id is not None:
        existing = await session.scalar(query)
        if existing is not None:
            if (existing.subject, existing.body) != (payload.subject, payload.body):
                raise ApiError(409, "ticket_key_conflict", "Request key was already used")
            return existing
    item = SupportTicket(
        **payload.model_dump(), requester_user_id=actor.id, status="OPEN", created_at=utc_now()
    )
    session.add(item)
    try:
        await session.flush()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="SUPPORT_TICKET_CREATED",
            entity_type="support_ticket",
            entity_id=item.id,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if payload.client_request_id is None:
            raise
        existing = await session.scalar(query)
        if existing is None:
            raise
        if (existing.subject, existing.body) != (payload.subject, payload.body):
            raise ApiError(409, "ticket_key_conflict", "Request key was already used") from None
        return existing
    return item


@router.get("/me/support-tickets", response_model=list[TicketRead])
async def own_tickets(
    session: Session, actor: Chatter, limit: Limit = 50, offset: Offset = 0
) -> list[SupportTicket]:
    return list(
        (
            await session.scalars(
                select(SupportTicket)
                .where(SupportTicket.requester_user_id == actor.id)
                .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.get("/admin/support-tickets", response_model=list[TicketRead])
async def support_queue(
    session: Session, _: Publisher, limit: Limit = 50, offset: Offset = 0
) -> list[SupportTicket]:
    return list(
        (
            await session.scalars(
                select(SupportTicket)
                .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.post("/admin/support-tickets/{item_id}/resolve", response_model=TicketRead)
async def resolve_ticket(item_id: UUID, session: Session, actor: Publisher) -> SupportTicket:
    item = await session.get(SupportTicket, item_id)
    if item is None:
        raise ApiError(404, "ticket_not_found", "Support ticket was not found")
    if item.status != "RESOLVED":
        item.status, item.resolved_at = "RESOLVED", utc_now()
        add_audit_event(
            session,
            actor_user_id=actor.id,
            action="SUPPORT_TICKET_RESOLVED",
            entity_type="support_ticket",
            entity_id=item.id,
        )
        await session.commit()
    return item
