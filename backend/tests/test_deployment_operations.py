import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[2] / "deploy" / (name + ".py")
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_environment_generation_preserves_existing_secrets_and_private_permissions(tmp_path):
    helper = module("generate_server_env")
    target = tmp_path / "server.env"
    helper.generate(None, target)
    before = target.read_bytes()
    assert "SERVER_DOMAIN=localhost" in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        helper.generate("api.example.invalid", target)
    assert target.read_bytes() == before
    with pytest.raises(ValueError):
        helper.generate("https://example.invalid/path", tmp_path / "bad.env")


async def test_read_only_financial_fingerprint_is_stable_on_postgres(context):
    if context.database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL read-only transaction semantics")
    helper = module("prepare_existing")
    async with context.database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32))"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('20261101_0037')"))
    before = await helper.fingerprint(context.settings.database_url)
    after = await helper.fingerprint(context.settings.database_url, before)
    assert before == after
    assert before["tables"]["expenses"]["rows"] == 0
    assert set(before["tables"]) == set(helper.TABLES)
