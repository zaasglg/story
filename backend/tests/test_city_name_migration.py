import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from conftest import AppContext
from sqlalchemy import select

from backend.identity.models import EmployeeProfile
from backend.workforce.models import City

spec = importlib.util.spec_from_file_location(
    "city_names_migration",
    Path(__file__).parents[1] / "alembic/versions/20261101_0022_russian_city_names.py",
)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def apply_migration(connection):
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


async def test_city_names_change_without_replacing_city_or_profile_links(
    context: AppContext,
) -> None:
    async with context.database.session_factory() as session:
        city = await session.scalar(select(City).where(City.name == "Lipetsk"))
        city_id = city.id
        city_timezone = city.timezone
        profiles = (await session.scalars(select(EmployeeProfile))).all()
        ids = {p.id: p.city_id for p in profiles}
        for profile in profiles:
            profile.city = "Lipetsk"
        await session.commit()
    for _ in range(2):
        async with context.database.engine.begin() as connection:
            await connection.run_sync(apply_migration)
    async with context.database.session_factory() as session:
        city = await session.get(City, city_id)
        assert city.name == "Липецк"
        assert city.timezone == city_timezone
        profiles = (await session.scalars(select(EmployeeProfile))).all()
        assert {p.id: p.city_id for p in profiles} == ids
        assert all(p.city == "Липецк" for p in profiles)
