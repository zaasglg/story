"""Private backup/financial fingerprint for an existing ALASH service; database READ ONLY."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

FINANCIAL_FIELDS = (
    "id",
    "request_id",
    "ledger_entry_id",
    "worker_user_id",
    "type",
    "amount",
    "currency",
    "period",
    "city_id",
    "object_id",
    "expense_date",
    "revenue_date",
    "category",
    "status",
    "work_date",
    "hours",
    "m3",
    "rate_per_m3",
    "daily_earnings",
    "old_amount",
    "new_amount",
    "revision",
    "created_at",
    "archived_at",
)
TABLES = (
    "ledger_entries",
    "ledger_corrections",
    "financial_requests",
    "expenses",
    "revenues",
    "work_days",
)


def private_write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)


def normalize(url: str) -> str:
    parsed = make_url(url).set(drivername="postgresql+asyncpg")
    query = dict(parsed.query)
    query.pop("channel_binding", None)
    mode = query.pop("sslmode", None)
    if mode and "ssl" not in query:
        query["ssl"] = mode
    return parsed.set(query=query).render_as_string(hide_password=False)


async def fingerprint(url: str, previous: dict | None = None) -> dict:
    engine = create_async_engine(normalize(url))
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                result = {
                    "revision": (
                        await connection.execute(text("SELECT version_num FROM alembic_version"))
                    ).scalar(),
                    "tables": {},
                }
                tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
                for table in TABLES:
                    if table not in tables:
                        continue
                    available = await connection.run_sync(
                        lambda c, table=table: {x["name"] for x in inspect(c).get_columns(table)}
                    )
                    fields = (
                        previous["tables"][table]["fields"]
                        if previous and table in previous["tables"]
                        else [field for field in FINANCIAL_FIELDS if field in available]
                    )
                    records = [
                        dict(row)
                        for row in (
                            await connection.execute(
                                text(
                                    "SELECT "
                                    + ",".join('"' + field + '"' for field in fields)
                                    + ' FROM "'
                                    + table
                                    + '"'
                                )
                            )
                        ).mappings()
                    ]
                    canonical = sorted(
                        json.dumps(row, default=str, sort_keys=True) for row in records
                    )
                    result["tables"][table] = {
                        "fields": fields,
                        "rows": len(records),
                        "sha256": hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
                    }
                return result
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "verify"])
    parser.add_argument("--pid", type=int)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, default=Path("/etc/m1917/backend.codex.env"))
    args = parser.parse_args()
    if args.mode == "verify":
        environment = dict(
            line.split("=", 1) for line in args.env_output.read_text().splitlines() if line
        )
        original = json.loads((args.directory / "financial-before.json").read_text())
        current = asyncio.run(fingerprint(environment["M1917_DATABASE_URL"], original))
        private_write(
            args.directory / "financial-after.json", json.dumps(current, indent=2).encode()
        )
        if original["tables"] != current["tables"]:
            raise SystemExit("Financial fingerprint differs: investigate before serving traffic")
        print(
            "Financial amounts, dimensions and record hashes preserved; schema="
            + str(current["revision"])
        )
        return
    if not args.pid:
        parser.error("--pid of the active existing service is required")
    environment = {}
    for item in Path(f"/proc/{args.pid}/environ").read_bytes().split(b"\0"):
        if item.startswith(b"M1917_") and b"=" in item:
            key, value = item.decode().split("=", 1)
            if "\n" in value:
                raise SystemExit("Multiline environment requires manual conversion: " + key)
            environment[key] = value
    for key in (
        "M1917_DATABASE_URL",
        "M1917_PII_ENCRYPTION_KEY",
        "M1917_JWT_SECRET",
        "M1917_OTP_PEPPER",
    ):
        if not environment.get(key):
            raise SystemExit("Existing service is missing required setting: " + key)
    environment["M1917_BOOTSTRAP_USERS_JSON"] = "[]"
    cipher = Fernet(environment["M1917_PII_ENCRYPTION_KEY"].encode())
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    private_write(args.env_output, "\n".join(f"{k}={v}" for k, v in environment.items()).encode())
    shutil.copyfile("/etc/m1917/backend.env", args.directory / "original-backend.env")
    (args.directory / "original-backend.env").chmod(0o600)
    before = asyncio.run(fingerprint(environment["M1917_DATABASE_URL"]))
    private_write(args.directory / "financial-before.json", json.dumps(before, indent=2).encode())
    url = make_url(environment["M1917_DATABASE_URL"])
    pg_environment = {**os.environ, "PGPASSWORD": url.password or ""}
    sslmode = url.query.get("sslmode") or url.query.get("ssl")
    if sslmode:
        pg_environment["PGSSLMODE"] = sslmode
    command = [
        "/usr/bin/pg_dump",
        "-h",
        url.host or "/var/run/postgresql",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "",
        "-d",
        url.database or "",
        "-Fc",
    ]
    with tempfile.TemporaryFile() as temporary:
        dumped = subprocess.run(
            command, env=pg_environment, stdout=temporary, stderr=subprocess.PIPE
        )
        if dumped.returncode:
            raise SystemExit("pg_dump failed; no migration is allowed until backup succeeds")
        if temporary.tell() > 512 * 1024 * 1024:
            raise SystemExit(
                "Archive exceeds memory-safe bound; arrange a streaming protected backup"
            )
        temporary.seek(0)
        archive = temporary.read()
    if not archive.startswith(b"PGDMP"):
        raise SystemExit("Unexpected dump format")
    encrypted = cipher.encrypt(archive)
    private_write(args.directory / "database.dump.fernet", encrypted)
    recovered = cipher.decrypt((args.directory / "database.dump.fernet").read_bytes())
    if hashlib.sha256(recovered).digest() != hashlib.sha256(archive).digest():
        raise SystemExit("Backup verification failed")
    checked = subprocess.run(
        ["/usr/bin/pg_restore", "--list"],
        input=recovered,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if checked.returncode:
        raise SystemExit("Backup archive index is invalid")
    private_write(
        args.directory / "backup.json",
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(encrypted).hexdigest(),
                "format": "Fernet(PostgreSQL custom dump)",
                "decryption_verified": True,
                "archive_index_verified": True,
            }
        ).encode(),
    )
    print(
        "Protected backup and read-only financial fingerprint verified; schema="
        + str(before["revision"])
    )


if __name__ == "__main__":
    main()
