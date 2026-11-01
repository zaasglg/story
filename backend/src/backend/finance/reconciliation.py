"""Read-only financial reconciliation. Findings are candidates, never automatic corrections."""

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.config import normalize_database_url


async def analyze(connection):
    # Explicit columns only: no passport, bank, worker name, phone, or free-text content.
    async def rows(table, columns):
        records = [
            dict(row)
            for row in (await connection.execute(text(f"SELECT {columns} FROM {table}"))).mappings()
        ]
        from datetime import date

        for record in records:
            for key in ("amount", "new_amount", "daily_earnings"):
                if key in record:
                    record[key] = Decimal(str(record[key]))
            for key in ("period", "work_date", "expense_date"):
                if isinstance(record.get(key), str):
                    record[key] = date.fromisoformat(record[key])
        return records

    ledger = await rows(
        "ledger_entries", "id,request_id,worker_user_id,type,amount,currency,period"
    )
    expenses = await rows(
        "expenses", "id,object_id,city_id,expense_date,amount,currency,category,status,archived_at"
    )
    requests = await rows(
        "financial_requests", "id,worker_user_id,type,amount,currency,period,status"
    )
    work = await rows(
        "work_days",
        "id,worker_user_id,object_id,work_date,daily_earnings,currency,status,archived_at",
    )
    objects = await rows("work_objects", "id,city_id")
    columns = await connection.run_sync(
        lambda conn: {column["name"] for column in inspect(conn).get_columns("expenses")}
    )
    corrections = await rows("ledger_corrections", "ledger_entry_id,revision,new_amount")
    effective = {}
    for correction in sorted(corrections, key=lambda row: row["revision"]):
        effective[correction["ledger_entry_id"]] = correction["new_amount"]
    links = {}
    if "payroll_entry_id" in columns:
        links = dict(
            (
                await connection.execute(
                    text(
                        "SELECT id,payroll_entry_id FROM expenses "
                        "WHERE payroll_entry_id IS NOT NULL"
                    )
                )
            ).all()
        )
    by_request = {r["id"]: r for r in requests}
    object_cities = {o["id"]: o["city_id"] for o in objects}
    findings = []

    def issue(kind, record, explanation, related=None, expected=None, observed=None):
        findings.append(
            {
                "kind": kind,
                "record_id": str(record["id"]),
                "currency": record.get("currency"),
                "amount": str(record.get("amount", "0")),
                "related_id": str(related) if related else None,
                "explanation": explanation,
                "expected_amount": str(expected) if expected is not None else None,
                "observed_amount": str(observed) if observed is not None else None,
                "delta": str(expected - observed)
                if expected is not None and observed is not None
                else None,
            }
        )

    for entry in ledger:
        request = by_request.get(entry["request_id"])
        if not request or request["status"] != "APPROVED":
            issue("LEDGER_WITHOUT_APPROVED_REQUEST", entry, "Ledger has no approved source request")
        elif (entry["worker_user_id"], entry["currency"]) != (
            request["worker_user_id"],
            request["currency"],
        ):
            issue("LEDGER_SOURCE_MISMATCH", entry, "Worker/currency differs from approved request")
        if entry["type"] in ("SALARY", "BONUS", "PENALTY"):
            linked = [
                e
                for e in expenses
                if links.get(e["id"]) == entry["id"] and e["archived_at"] is None
            ]
            amount = effective.get(entry["id"], entry["amount"])
            signed = -amount if entry["type"] == "PENALTY" else amount
            observed = sum(
                (e["amount"] for e in linked if e["currency"] == entry["currency"]), Decimal("0")
            )
            if not linked:
                issue(
                    "UNLINKED_PAYROLL_COST",
                    entry,
                    "Cost requires explicit legacy reconciliation",
                    expected=signed,
                    observed=Decimal("0"),
                )
            else:
                if any(e["currency"] != entry["currency"] for e in linked):
                    issue("LINK_CURRENCY_MISMATCH", entry, "Linked expense has another currency")
                if observed != signed:
                    issue(
                        "PAYROLL_COST_MISMATCH",
                        entry,
                        "Linked costs differ from effective ledger",
                        expected=signed,
                        observed=observed,
                    )
            if not any(
                w["worker_user_id"] == entry["worker_user_id"]
                and w["currency"] == entry["currency"]
                and w["status"] == "APPROVED"
                and w["archived_at"] is None
                and w["work_date"].replace(day=1) == entry["period"].replace(day=1)
                for w in work
            ):
                issue(
                    "NO_APPROVED_WORKDAY",
                    entry,
                    "No approved work-day evidence for this worker/month/currency",
                )
    active = [e for e in expenses if e["archived_at"] is None]
    for expense in active:
        if expense["object_id"] is None:
            issue("NO_OBJECT", expense, "City-level cost: allocation must be reviewed, not guessed")
        elif object_cities.get(expense["object_id"]) != expense["city_id"]:
            issue("OBJECT_CITY_MISMATCH", expense, "Object belongs to another city")
    fingerprints = defaultdict(list)
    for expense in active:
        fingerprints[
            tuple(
                expense[k]
                for k in ("object_id", "city_id", "expense_date", "amount", "currency", "category")
            )
        ].append(expense)
    for candidates in fingerprints.values():
        if len(candidates) > 1:
            for expense in candidates:
                issue(
                    "POSSIBLE_DUPLICATE",
                    expense,
                    "Same dimensions/amount; not proven duplicate",
                    candidates[0]["id"],
                )
    totals = defaultdict(lambda: Decimal("0"))
    for expense in active:
        totals[expense["currency"]] += expense["amount"]
    review_totals = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    delta_totals = defaultdict(lambda: Decimal("0"))
    for finding in findings:
        review_totals[finding["kind"]][finding["currency"]] += Decimal(finding["amount"])
        if finding["delta"] is not None:
            delta_totals[finding["currency"]] += Decimal(finding["delta"])
    return {
        "mode": "READ_ONLY",
        "review_amounts_by_kind_currency": {
            kind: {currency: str(value) for currency, value in totals.items()}
            for kind, totals in review_totals.items()
        },
        "payroll_cost_delta_by_currency": {
            currency: str(value) for currency, value in delta_totals.items()
        },
        "counts": {
            "ledger": len(ledger),
            "expenses": len(expenses),
            "financial_requests": len(requests),
            "work_days": len(work),
        },
        "active_expense_totals": {k: str(v) for k, v in totals.items()},
        "finding_counts": dict(Counter(f["kind"] for f in findings)),
        "affected_records": len({f["record_id"] for f in findings}),
        "findings": findings,
        "correction_plan": [
            {
                "record_id": f["record_id"],
                "kind": f["kind"],
                "action": "REVIEW_ONLY",
                "expected_amount": f["amount"],
                "apply_allowed": False,
            }
            for f in findings
        ],
    }


async def run(url):
    engine = create_async_engine(normalize_database_url(url))
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                if connection.dialect.name == "postgresql":
                    await connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                return await analyze(connection)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        required=True,
        help="Name of environment variable; never print its value",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(os.environ[args.database_url_env]))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
