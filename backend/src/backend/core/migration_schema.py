"""Reconcile feature DDL with installations that already contain those features."""

import sqlalchemy as sa

from alembic import context, op


def _inspector():
    return sa.inspect(op.get_bind())


def _check_column(table: str, actual: dict, expected: sa.Column) -> None:
    dialect = op.get_bind().dialect
    actual_type = actual["type"].compile(dialect=dialect)
    expected_type = expected.type.compile(dialect=dialect)
    if actual_type != expected_type or actual["nullable"] != expected.nullable:
        raise RuntimeError(
            f"Incompatible existing column {table}.{expected.name}: "
            f"{actual_type}, nullable={actual['nullable']}; "
            f"expected {expected_type}, nullable={expected.nullable}. "
            "Inspect the schema before retrying; existing data was not replaced."
        )


def add_column(table: str, column: sa.Column) -> bool:
    """Add a missing column, or validate an existing one; return whether added."""
    if not context.is_offline_mode():
        existing = {item["name"]: item for item in _inspector().get_columns(table)}
        if column.name in existing:
            _check_column(table, existing[column.name], column)
            return False
    op.add_column(table, column)
    return True


def create_table(name: str, *columns: sa.Column) -> bool:
    """Create a missing table, or validate its columns, primary key and references."""
    if context.is_offline_mode() or not _inspector().has_table(name):
        op.create_table(name, *columns)
        return True
    inspector = _inspector()
    actual = {item["name"]: item for item in inspector.get_columns(name)}
    for column in columns:
        if column.name not in actual:
            raise RuntimeError(f"Existing table {name} lacks required column {column.name}")
        _check_column(name, actual[column.name], column)
    expected_names = {column.name for column in columns}
    for item in actual.values():
        if item["name"] not in expected_names and not item["nullable"] and not item["default"]:
            raise RuntimeError(f"Unexpected required column {name}.{item['name']}")
    primary_key = inspector.get_pk_constraint(name)["constrained_columns"]
    if primary_key != [column.name for column in columns if column.primary_key]:
        raise RuntimeError(f"Incompatible existing primary key on {name}")
    for column in columns:
        for foreign_key in column.foreign_keys:
            target_table, target_column = foreign_key.target_fullname.rsplit(".", 1)
            ensure_foreign_key(
                f"fk_{name}_{column.name}",
                name,
                target_table,
                [column.name],
                [target_column],
                ondelete=foreign_key.ondelete,
            )
    return False


def ensure_foreign_key(
    name: str,
    source: str,
    target: str,
    local_columns: list[str],
    remote_columns: list[str],
    *,
    ondelete: str | None,
) -> None:
    if not context.is_offline_mode():
        for item in _inspector().get_foreign_keys(source):
            if item["constrained_columns"] == local_columns:
                actual_delete = item.get("options", {}).get("ondelete", "NO ACTION")
                if (
                    item["referred_table"] != target
                    or item["referred_columns"] != remote_columns
                    or actual_delete != ondelete
                ):
                    raise RuntimeError(f"Incompatible foreign key on {source}.{local_columns}")
                return
    op.create_foreign_key(name, source, target, local_columns, remote_columns, ondelete=ondelete)


def ensure_index(name: str, table: str, columns: list[str]) -> None:
    if not context.is_offline_mode():
        for item in _inspector().get_indexes(table):
            if item["name"] == name:
                if item["column_names"] != columns or item["unique"]:
                    raise RuntimeError(f"Incompatible existing index {name}")
                return
    op.create_index(name, table, columns)


def ensure_check(name: str, table: str, expression: str) -> None:
    """Enforce the expected expression without trusting a preexisting check's SQL."""
    if not context.is_offline_mode():
        existing = {item["name"] for item in _inspector().get_check_constraints(table)}
        if name in existing:
            # PostgreSQL rewrites reflected expressions. Add and validate our own
            # expression under a separate name instead of comparing SQL strings.
            name = name + "_reconciled"
            if name in existing:
                raise RuntimeError(f"Unexpected reconciliation constraint {name}; inspect schema")
    op.create_check_constraint(name, table, expression)
