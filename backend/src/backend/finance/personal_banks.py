"""Encrypted personal accounts, scoped to the authenticated executive."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select

from backend.authorization.dependencies import require_permission
from backend.authorization.permissions import Permission
from backend.core.config import Settings
from backend.core.crypto import PiiCipher
from backend.core.errors import ApiError
from backend.core.time import utc_now
from backend.finance.company import Session, SettingsDep, audit
from backend.finance.models import PersonalBankAccount
from backend.identity.dependencies import CurrentUser
from backend.workforce.schemas import SalaryAccountWrite

router = APIRouter(prefix="/boss/personal-bank-accounts", tags=["personal-bank-accounts"])
Owner = Annotated[CurrentUser, Depends(require_permission(Permission.PERSONAL_BANK_MANAGE))]


class PersonalBankWrite(SalaryAccountWrite):
    name: str = Field(min_length=1, max_length=255)


class PersonalBankRead(PersonalBankWrite):
    id: UUID


def read(item: PersonalBankAccount, settings: Settings) -> PersonalBankRead:
    details = PersonalBankWrite.model_validate_json(
        PiiCipher(settings.pii_encryption_key.get_secret_value()).decrypt(item.details_encrypted)
    )
    return PersonalBankRead(id=item.id, **details.model_dump())


async def owned(session: Session, item_id: UUID, actor: CurrentUser) -> PersonalBankAccount:
    item = await session.scalar(
        select(PersonalBankAccount).where(
            PersonalBankAccount.id == item_id,
            PersonalBankAccount.owner_user_id == actor.id,
            PersonalBankAccount.archived_at.is_(None),
        )
    )
    if item is None:
        raise ApiError(404, "bank_not_found", "Bank account was not found")
    return item


@router.get("", response_model=list[PersonalBankRead])
async def list_accounts(
    session: Session, actor: Owner, settings: SettingsDep, request: Request
) -> list[PersonalBankRead]:
    rows = (
        await session.scalars(
            select(PersonalBankAccount)
            .where(
                PersonalBankAccount.owner_user_id == actor.id,
                PersonalBankAccount.archived_at.is_(None),
            )
            .order_by(PersonalBankAccount.created_at.desc(), PersonalBankAccount.id)
        )
    ).all()
    result = [read(item, settings) for item in rows]
    audit(session, request, actor, "PERSONAL_BANK_READ", "personal_bank", actor.id)
    await session.commit()
    return result


@router.post("", response_model=PersonalBankRead, status_code=201)
async def create_account(
    payload: PersonalBankWrite,
    session: Session,
    actor: Owner,
    settings: SettingsDep,
    request: Request,
) -> PersonalBankRead:
    item = PersonalBankAccount(
        owner_user_id=actor.id,
        created_at=utc_now(),
        details_encrypted=PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(
            payload.model_dump_json()
        ),
    )
    session.add(item)
    await session.flush()
    result = read(item, settings)
    audit(session, request, actor, "PERSONAL_BANK_CREATED", "personal_bank", item.id)
    await session.commit()
    return result


@router.put("/{item_id}", response_model=PersonalBankRead)
async def update_account(
    item_id: UUID,
    payload: PersonalBankWrite,
    session: Session,
    actor: Owner,
    settings: SettingsDep,
    request: Request,
) -> PersonalBankRead:
    item = await owned(session, item_id, actor)
    item.details_encrypted = PiiCipher(settings.pii_encryption_key.get_secret_value()).encrypt(
        payload.model_dump_json()
    )
    result = read(item, settings)
    audit(session, request, actor, "PERSONAL_BANK_UPDATED", "personal_bank", item.id)
    await session.commit()
    return result


@router.delete("/{item_id}", status_code=204)
async def delete_account(item_id: UUID, session: Session, actor: Owner, request: Request) -> None:
    item = await owned(session, item_id, actor)
    item.archived_at = utc_now()
    audit(session, request, actor, "PERSONAL_BANK_ARCHIVED", "personal_bank", item.id)
    await session.commit()
