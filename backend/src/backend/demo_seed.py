"""Populate a development database with a repeatable, linked demo dataset."""

import argparse
import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access.models import WorkAuthorization
from backend.access.rating import sync_attendance_rating
from backend.audit.service import add_audit_event
from backend.communications.models import (
    Announcement,
    ChatMessage,
    ChatThread,
    Notification,
    SupportTicket,
)
from backend.core.config import get_settings
from backend.core.database import Database
from backend.core.time import utc_now
from backend.equipment.models import EquipmentIssue, EquipmentItem, StockMovement
from backend.finance.models import Expense, FinancialRequest, LedgerEntry, Revenue
from backend.identity.models import AccountStatus, Base, EmployeeProfile, User, UserRole
from backend.identity.security import hash_password
from backend.operations.models import (
    AttendanceRecord,
    AttendanceStatus,
    TariffHistory,
    WorkDay,
    WorkDayStatus,
    WorkSchedule,
)
from backend.workforce.models import (
    Brigade,
    City,
    ObjectStatus,
    ProfileChangeRequest,
    WorkerAssignment,
    WorkObject,
)


def demo_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"alash-m1917/demo/v1/{key}")


async def put(session: AsyncSession, model: type[Base], seed_key: str, **values):
    """Only insert owned rows; never overwrite an existing demo row."""
    item = await session.get(model, demo_id(seed_key))
    if item is None:
        item = model(id=demo_id(seed_key), **values)
        session.add(item)
        await session.flush()
    return item


async def populate_demo(session: AsyncSession, today: date) -> dict[str, int]:
    now = utc_now()
    period = today.replace(day=1)
    roles = {}
    for role, phone in [
        (UserRole.BOSS, "+77000000001"),
        (UserRole.ADMIN, "+77000000002"),
        (UserRole.MASTER, "+77000000003"),
        (UserRole.WORKER, "+77000000004"),
    ]:
        user = await session.scalar(select(User).where(User.phone == phone, User.role == role))
        if user is None:
            raise ValueError(f"Bootstrap demo account {phone} ({role}) is required")
        roles[role] = user
    admin, boss, master = (roles[r] for r in (UserRole.ADMIN, UserRole.BOSS, UserRole.MASTER))
    cities = []
    for legacy, name in [("Lipetsk", "Липецк"), ("Kursk", "Курск"), ("Voronezh", "Воронеж")]:
        city = await session.scalar(select(City).where(City.name.in_([legacy, name])))
        if city is not None:
            city.name = name
        if city is None:
            city = await put(
                session, City, f"city/{legacy}", name=name, timezone="Europe/Moscow", created_at=now
            )
        cities.append(city)
    brigades, objects, rates = [], [], []
    for i, (name, address) in enumerate(
        [
            ("ЖК Северный", "Липецк, ул. Строителей, 10"),
            ("Склад Восточный", "Курск, ул. Промышленная, 24"),
            ("Бизнес-центр Центральный", "Воронеж, ул. Мира, 7"),
        ]
    ):
        brigade = await put(
            session,
            Brigade,
            f"brigade/{i}",
            name=f"Бригада №{i + 1}",
            city_id=cities[i].id,
            master_user_id=master.id,
            created_at=now,
            updated_at=now,
        )
        obj = await put(
            session,
            WorkObject,
            f"object/{i}",
            name=name,
            address=address,
            city_id=cities[i].id,
            status=ObjectStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        rate = Decimal(450 + i * 75)
        if not await session.scalar(
            select(TariffHistory).where(
                TariffHistory.object_id == obj.id,
                TariffHistory.effective_to.is_(None),
                TariffHistory.archived_at.is_(None),
            )
        ):
            await put(
                session,
                TariffHistory,
                f"tariff/{i}",
                object_id=obj.id,
                rate_per_m3=rate,
                currency="RUB",
                effective_from=period,
                created_by=admin.id,
                created_at=now,
            )
        brigades.append(brigade)
        objects.append(obj)
        rates.append(rate)
    workers = [roles[UserRole.WORKER]]
    names = [
        "Асан Оразғали",
        "Ерлан Сейітов",
        "Данияр Қасымов",
        "Нұрлан Әбілов",
        "Марат Иванов",
        "Руслан Ахметов",
        "Айбек Жұмабаев",
        "Сергей Петров",
    ]
    for i, name in enumerate(names):
        phone = f"+770000001{i + 1:02d}"
        worker = await session.scalar(select(User).where(User.phone == phone))
        if worker is None:
            worker = await put(
                session,
                User,
                f"worker/{i}",
                phone=phone,
                password_hash=hash_password("Demo-worker-2026!"),
                role=UserRole.WORKER,
                status=AccountStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            await put(
                session,
                EmployeeProfile,
                f"profile/{i}",
                user_id=worker.id,
                full_name=name,
                employee_id=f"STAFF-{i + 1:03d}",
                city=cities[(i + 1) % 3].name,
                city_id=cities[(i + 1) % 3].id,
                master_user_id=master.id,
                hire_date=today - timedelta(days=90 + i * 10),
                created_at=now,
                updated_at=now,
            )
        elif worker.id != demo_id(f"worker/{i}"):
            raise ValueError(f"Demo phone {phone} is already used by another account")
        workers.append(worker)
    for i, worker in enumerate(workers):
        brigade, obj, rate = brigades[i % 3], objects[i % 3], rates[i % 3]
        assignment = await session.scalar(
            select(WorkerAssignment).where(
                WorkerAssignment.worker_user_id == worker.id,
                WorkerAssignment.effective_to.is_(None),
                WorkerAssignment.archived_at.is_(None),
            )
        )
        if assignment is None:
            await put(
                session,
                WorkerAssignment,
                f"assignment/{i}",
                worker_user_id=worker.id,
                brigade_id=brigade.id,
                object_id=obj.id,
                master_user_id=master.id,
                effective_from=now - timedelta(days=20),
                assigned_by=admin.id,
                created_at=now,
            )
            profile = await session.scalar(
                select(EmployeeProfile).where(EmployeeProfile.user_id == worker.id)
            )
            if profile is not None:
                profile.master_user_id = master.id
                profile.city_id, profile.city = cities[i % 3].id, cities[i % 3].name
        elif assignment.id != demo_id(f"assignment/{i}"):
            continue  # Keep existing employee assignments and their linked operations intact.
        await put(
            session,
            WorkAuthorization,
            f"authorization/{i}/{today}",
            worker_user_id=worker.id,
            valid_from=today - timedelta(days=30),
            valid_until=today + timedelta(days=90),
            reference=f"PASS-{i + 1:03d}",
            created_by=admin.id,
            created_at=now,
        )
        earnings = Decimal(0)
        for offset in range(-7, 8):
            day = today + timedelta(days=offset)
            key = f"{i}/{day}"
            if not await session.scalar(
                select(WorkSchedule).where(
                    WorkSchedule.worker_user_id == worker.id,
                    WorkSchedule.work_date == day,
                )
            ):
                await put(
                    session,
                    WorkSchedule,
                    f"schedule/{key}",
                    worker_user_id=worker.id,
                    brigade_id=brigade.id,
                    object_id=obj.id,
                    work_date=day,
                    shift_start=time(8),
                    shift_end=time(17),
                    created_by=admin.id,
                    updated_by=admin.id,
                    created_at=now,
                    updated_at=now,
                )
            if offset >= 0:
                continue
            absent = i == 2 and offset == -3
            check_in = datetime.combine(day, time(8), ZoneInfo("Europe/Moscow"))
            check_out = datetime.combine(day, time(17), ZoneInfo("Europe/Moscow"))
            if not await session.scalar(
                select(AttendanceRecord).where(
                    AttendanceRecord.worker_user_id == worker.id,
                    AttendanceRecord.work_date == day,
                )
            ):
                await put(
                    session,
                    AttendanceRecord,
                    f"attendance/{key}",
                    worker_user_id=worker.id,
                    work_date=day,
                    check_in=None if absent else check_in,
                    check_out=None if absent else check_out,
                    status=AttendanceStatus.ABSENT if absent else AttendanceStatus.PRESENT,
                    comment="Отметка посещаемости",
                    created_by=admin.id,
                    updated_by=admin.id,
                    created_at=now,
                    updated_at=now,
                )
            if absent:
                continue
            volume = Decimal(8 + i + (offset % 3))
            status = WorkDayStatus.DRAFT if offset == -1 else WorkDayStatus.APPROVED
            if day >= period and status == WorkDayStatus.APPROVED:
                earnings += volume * rate
            if not await session.scalar(
                select(WorkDay).where(
                    WorkDay.worker_user_id == worker.id,
                    WorkDay.object_id == obj.id,
                    WorkDay.work_date == day,
                )
            ):
                await put(
                    session,
                    WorkDay,
                    f"work-day/{key}",
                    worker_user_id=worker.id,
                    object_id=obj.id,
                    brigade_id=brigade.id,
                    work_date=day,
                    check_in=check_in,
                    check_out=check_out,
                    hours=Decimal(8),
                    m3=volume,
                    rate_per_m3=rate,
                    daily_earnings=volume * rate,
                    currency="RUB",
                    status=status,
                    created_by=admin.id,
                    updated_by=admin.id,
                    created_at=now,
                    updated_at=now,
                )
        await sync_attendance_rating(session, worker.id, admin.id)
        for kind, amount, approved in [
            ("SALARY", earnings, True),
            ("ADVANCE", Decimal(5000), True),
            ("BONUS", Decimal(2000), False),
        ]:
            if amount <= 0:
                continue
            key = f"finance/{i}/{period}/{kind}"
            if kind == "SALARY" and await session.scalar(
                select(LedgerEntry).where(
                    LedgerEntry.worker_user_id == worker.id,
                    LedgerEntry.period == period,
                    LedgerEntry.type == kind,
                    LedgerEntry.currency == "RUB",
                    LedgerEntry.id != demo_id(f"ledger/{key}"),
                )
            ):
                continue
            req = await put(
                session,
                FinancialRequest,
                key,
                worker_user_id=worker.id,
                type=kind,
                amount=amount,
                currency="RUB",
                period=period,
                reason="Оплата работ" if kind == "SALARY" else "Выплата",
                status="APPROVED" if approved else "PENDING_BOSS",
                idempotency_key=key,
                created_by=admin.id,
                reviewed_by=boss.id if approved else None,
                reviewed_at=now if approved else None,
                created_at=now,
            )
            if approved:
                await put(
                    session,
                    LedgerEntry,
                    f"ledger/{key}",
                    request_id=req.id,
                    worker_user_id=worker.id,
                    type=kind,
                    amount=amount,
                    currency="RUB",
                    period=period,
                    created_at=now,
                )
    for i, name in enumerate(["Перфоратор", "Защитная каска", "Лазерный уровень", "Шуруповёрт"]):
        item = await put(
            session,
            EquipmentItem,
            f"equipment/{i}",
            name=name,
            serial_number=f"EQ-{i + 1:03d}",
            available_quantity=9,
            created_at=now,
            updated_at=now,
        )
        await put(
            session,
            StockMovement,
            f"stock/{i}",
            equipment_id=item.id,
            delta=10,
            reason="Начальный остаток",
            actor_user_id=admin.id,
            created_at=now,
        )
        await put(
            session,
            EquipmentIssue,
            f"issue/{i}",
            equipment_id=item.id,
            worker_user_id=workers[i].id,
            quantity=1,
            issue_date=today - timedelta(days=5),
            created_by=admin.id,
            created_at=now,
        )
        await put(
            session,
            StockMovement,
            f"stock-issue/{i}",
            equipment_id=item.id,
            delta=-1,
            reason="Выдача работнику",
            actor_user_id=admin.id,
            created_at=now,
        )
    for i, obj in enumerate(objects):
        common = dict(
            object_id=obj.id,
            city_id=cities[i].id,
            currency="RUB",
            created_by=admin.id,
            updated_by=admin.id,
            created_at=now,
            updated_at=now,
        )
        await put(
            session,
            Expense,
            f"expense/{i}/{period}",
            expense_date=today,
            amount=Decimal(25000 + i * 10000),
            category="material",
            comment="Закупка строительных материалов",
            status="APPROVED",
            **common,
        )
        await put(
            session,
            Revenue,
            f"revenue/{i}/{period}",
            revenue_date=today,
            amount=Decimal(300000 + i * 75000),
            category="Оплата заказчика",
            comment="Оплата выполненных работ",
            status="APPROVED",
            **common,
        )
    for i, (title, body) in enumerate(
        [
            (
                "План работ на неделю",
                "Смены начинаются в 08:00. Расписание доступно в календаре.",
            ),
            (
                "Техника безопасности",
                "Перед началом смены проверьте каску и защитные перчатки.",
            ),
            (
                "Выплаты",
                "Начисления доступны в разделе финансов. Премии ожидают согласования.",
            ),
        ]
    ):
        announcement = await put(
            session,
            Announcement,
            f"announcement/{i}",
            title=title,
            body=body,
            created_by=admin.id,
            created_at=now,
        )
        for worker in workers:
            await put(
                session,
                Notification,
                f"notification/{i}/{worker.id}",
                recipient_user_id=worker.id,
                title=title,
                body=body,
                announcement_id=announcement.id,
                created_at=now,
            )
    thread = await session.scalar(select(ChatThread).where(ChatThread.key == "GENERAL"))
    if thread is None:
        thread = await put(session, ChatThread, "general-chat", key="GENERAL", created_at=now)
    for i, (sender, body) in enumerate(
        [
            (admin, "Добро пожаловать! Расписание на неделю уже опубликовано."),
            (master, "Инструменты получены, бригады готовы к работе."),
            (workers[0], "Свою смену вижу. Спасибо!"),
        ]
    ):
        await put(
            session,
            ChatMessage,
            f"message/{i}",
            thread_id=thread.id,
            sender_user_id=sender.id,
            client_message_id=f"demo-message-{i}",
            body=body,
            created_at=now - timedelta(minutes=10 - i),
        )
    for i, worker in enumerate(workers[:3]):
        await put(
            session,
            SupportTicket,
            f"ticket/{i}",
            requester_user_id=worker.id,
            client_request_id=f"demo-ticket-{i}",
            subject="Вопрос по расписанию",
            body="Прошу уточнить время выдачи инструментов перед сменой.",
            status="RESOLVED" if i == 2 else "OPEN",
            created_at=now,
            resolved_at=now if i == 2 else None,
        )
    await put(
        session,
        ProfileChangeRequest,
        "profile-change",
        worker_user_id=workers[0].id,
        changes={"full_name": "Оразғали Асан"},
        reason="Уточнение имени",
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    add_audit_event(
        session,
        action="DEMO_DATA_SEEDED",
        entity_type="demo",
        actor_user_id=admin.id,
        metadata={"anchor_date": today.isoformat()},
    )
    await session.commit()
    return {"workers": len(workers), "brigades": len(brigades), "objects": len(objects)}


async def seed_demo(today: date) -> None:
    settings = get_settings()
    if settings.environment != "development":
        raise ValueError("Demo seed is only available in development")
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            print(await populate_demo(session, today))
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=utc_now().astimezone(ZoneInfo("Asia/Almaty")).date(),
    )
    args = parser.parse_args()
    asyncio.run(seed_demo(args.date))


if __name__ == "__main__":
    main()
