"""Alembic: 0001_initial upgrade/downgrade on a temporary SQLite database, no pending model changes,
and PostgreSQL DDL rendering (offline mode, no server needed)."""

import io
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_config(url: str, output_buffer: io.StringIO | None = None) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_single_head_is_initial_revision() -> None:
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert script.get_heads() == ["0001_initial"]
    revision = script.get_revision("0001_initial")
    assert revision is not None
    assert revision.down_revision is None


def test_upgrade_downgrade_upgrade_without_pending_changes(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
    config = _alembic_config(url)
    engine = sa.create_engine(url)
    expected = set(Base.metadata.tables) | {"alembic_version"}
    try:
        command.upgrade(config, "head")
        assert _tables(engine) == expected

        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []

        command.downgrade(config, "base")
        assert _tables(engine) == {"alembic_version"}

        command.upgrade(config, "head")
        assert _tables(engine) == expected

        inspector = sa.inspect(engine)
        assert "ck_order_items_quantity_positive" in {c["name"] for c in inspector.get_check_constraints("order_items")}
        assert {"ck_products_price_positive", } <= {c["name"] for c in inspector.get_check_constraints("products")}
        assert "ck_payments_amount_positive" in {c["name"] for c in inspector.get_check_constraints("payments")}
        assert "ck_orders_status" in {c["name"] for c in inspector.get_check_constraints("orders")}
        assert "uq_customers_instagram_user_id" in {u["name"] for u in inspector.get_unique_constraints("customers")}
        order_indexes = {index["name"] for index in inspector.get_indexes("orders")}
        assert {
            "ix_orders_customer_id",
            "ix_orders_status",
            "ix_orders_delivery_date",
            "ix_orders_delivery_date_status",
            "ix_orders_customer_id_status",
        } <= order_indexes
        assert "ix_messages_conversation_id_created_at" in {i["name"] for i in inspector.get_indexes("messages")}
    finally:
        engine.dispose()


def _reflect_schema(engine: sa.Engine) -> dict[str, dict[str, object]]:
    """Everything autogenerate does not compare: server defaults, FK ON DELETE, CHECK constraints."""
    inspector = sa.inspect(engine)
    schema: dict[str, dict[str, object]] = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        schema[table] = {
            "columns": {
                c["name"]: (str(c["type"]), c["nullable"], None if c["default"] is None else str(c["default"]))
                for c in inspector.get_columns(table)
            },
            "pk": inspector.get_pk_constraint(table),
            "fks": sorted(
                (
                    str(fk["name"]),
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(sorted((fk.get("options") or {}).items())),
                )
                for fk in inspector.get_foreign_keys(table)
            ),
            "uniques": sorted((str(u["name"]), tuple(u["column_names"])) for u in inspector.get_unique_constraints(table)),
            "indexes": sorted(
                (str(i["name"]), tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)
            ),
            "checks": sorted((str(c["name"]), " ".join(c["sqltext"].split())) for c in inspector.get_check_constraints(table)),
        }
        if engine.dialect.name == "sqlite":
            # SQLAlchemy's SQLite FK reflection does not reliably report ON DELETE; the pragma does.
            with engine.connect() as connection:
                rows = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').mappings().all()
            schema[table]["fk_actions"] = sorted(
                (row["from"], row["table"], row["to"], row["on_update"], row["on_delete"]) for row in rows
            )
    return schema


def test_migration_schema_matches_models_including_defaults_checks_and_ondelete(tmp_path: Path) -> None:
    migrated_url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    created_url = f"sqlite:///{(tmp_path / 'created.db').as_posix()}"
    command.upgrade(_alembic_config(migrated_url), "head")
    migrated, created = sa.create_engine(migrated_url), sa.create_engine(created_url)
    try:
        Base.metadata.create_all(created)
        migrated_schema, created_schema = _reflect_schema(migrated), _reflect_schema(created)
    finally:
        migrated.dispose()
        created.dispose()
    assert set(migrated_schema) == set(Base.metadata.tables)
    for table in sorted(created_schema):
        assert migrated_schema[table] == created_schema[table], table


def _pg_create_table_parts(sql: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\)", sql, re.S):
        body = " ".join(match.group(2).split())
        tables[match.group(1)] = {part.strip() for part in re.split(r",\s+(?=[a-z_]+ [A-Z]|CONSTRAINT )", body)}
    return tables


def test_postgresql_migration_ddl_matches_models() -> None:
    buffer = io.StringIO()
    command.upgrade(_alembic_config("postgresql+psycopg://u:p@localhost/db", output_buffer=buffer), "head", sql=True)
    migration_sql = buffer.getvalue()

    dialect = postgresql.dialect()
    model_sql = "\n".join(str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables)
    migration_tables = _pg_create_table_parts(migration_sql)
    migration_tables.pop("alembic_version")
    assert migration_tables == _pg_create_table_parts(model_sql)

    def normalized_indexes(sql: str) -> set[str]:
        return {" ".join(m.group(0).split()) for m in re.finditer(r"CREATE (?:UNIQUE )?INDEX [^;]+", sql)}

    model_indexes = {
        " ".join(str(CreateIndex(index).compile(dialect=dialect)).split())
        for table in Base.metadata.sorted_tables
        for index in table.indexes
    }
    assert normalized_indexes(migration_sql) == model_indexes


def test_postgresql_offline_sql_renders() -> None:
    buffer = io.StringIO()
    config = _alembic_config("postgresql+psycopg://bakery:bakery@localhost:5432/bakery", output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()

    assert "CREATE TABLE orders" in sql
    assert "CREATE TABLE daily_reports" in sql
    assert "CONSTRAINT ck_orders_status CHECK" in sql
    assert "CONSTRAINT ck_order_items_quantity_positive CHECK (quantity > 0)" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "NUMERIC(12, 2)" in sql
    assert "NUMERIC(9, 6)" in sql
    assert "DEFAULT true" in sql
    assert "ON DELETE CASCADE" in sql
