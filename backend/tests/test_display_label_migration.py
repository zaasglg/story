import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import AppContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.identity.models import AuditLog, EmployeeProfile, User, UserRole

spec = importlib.util.spec_from_file_location(
    "clean_labels",
    Path(__file__).parents[1] / "alembic/versions/20261101_0021_clean_display_labels.py",
)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def apply_migration(connection):
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


async def test_normalization_preserves_accounts_and_is_repeatable(context: AppContext) -> None:
    async with context.database.session_factory() as session:
        user = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == user.id)
        )
        profile.employee_id = "DEMO-006"
        profile.full_name = "Development Admin"
        credentials = (user.id, user.phone, user.password_hash, user.auth_version)
        await session.commit()
    for _ in range(2):
        async with context.database.engine.begin() as connection:
            await connection.run_sync(apply_migration)
    async with context.database.session_factory() as session:
        user = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        profile = await session.scalar(
            select(EmployeeProfile).where(EmployeeProfile.user_id == user.id)
        )
        assert profile.employee_id == "EMP-006"
        assert profile.full_name == "Администратор"
        assert (user.id, user.phone, user.password_hash, user.auth_version) == credentials
        audit = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "DISPLAY_LABELS_NORMALIZED")
            )
        ).all()
        assert len(audit) == 1


async def test_duplicate_employee_number_rolls_back(context: AppContext) -> None:
    async with context.database.session_factory() as session:
        profiles = (await session.scalars(select(EmployeeProfile))).all()
        profiles[0].employee_id = "EMP-006"
        profiles[1].employee_id = "DEMO-006"
        await session.commit()
    with pytest.raises(IntegrityError):
        async with context.database.engine.begin() as connection:
            await connection.run_sync(apply_migration)
    async with context.database.session_factory() as session:
        values = (await session.scalars(select(EmployeeProfile.employee_id))).all()
        assert "EMP-006" in values
        assert "DEMO-006" in values


@pytest.mark.parametrize(
    "value,column,expected",
    [
        ("ДЕМО · ЖК Северный", "name", "ЖК Северный"),
        ("ДЕМО · Бригада №1", "name", "Бригада №1"),
        ("DEMO-EQ-001", "serial_number", "EQ-001"),
        ("DEMO-PASS-001", "reference", "PASS-001"),
        ("Демонстрационная запись", "comment", "Отметка посещаемости"),
        ("Руслан Ахметов", "full_name", "Руслан Ахметов"),
        ("Демократическая улица", "address", "Демократическая улица"),
    ],
)
def test_display_labels(value, column, expected) -> None:
    assert migration.clean_label(value, column) == expected
