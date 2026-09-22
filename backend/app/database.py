from collections.abc import Generator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

settings = get_settings()
engine = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False, future=True)


def rebind_engine(database_url: str | None = None) -> None:
    """Rebuild the engine after tests change DATABASE_URL / settings cache."""
    global engine, settings
    # Dispose the outgoing engine first. Leaking it keeps its pooled SQLite
    # connections open, which on Windows blocks deleting the database file.
    if engine is not None:
        engine.dispose()
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    SessionLocal.configure(bind=engine)


def dispose_engine() -> None:
    """Close pooled connections so the backing file can be deleted.

    Windows refuses to unlink a SQLite file while a connection is still open,
    so test teardown must call this before removing its database.
    """
    global engine
    if engine is not None:
        engine.dispose()


rebind_engine()


class Base(DeclarativeBase):
    """Declarative base shared by all models (portable across SQLite and PostgreSQL)."""


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_legacy_role_values() -> int:
    """Rewrite legacy role strings to their canonical equivalents.

    Roles are stored as VARCHAR, so old rows can still hold 'admin',
    'dispatcher', 'program_manager' or 'viewer'. Authorization resolves these at
    runtime, but normalizing the stored values lets the legacy enum members be
    retired once no rows reference them.
    """
    from app.rbac import CANONICAL_ROLE

    updated = 0
    with SessionLocal() as db:
        for legacy, canonical in CANONICAL_ROLE.items():
            updated += db.execute(
                text("UPDATE users SET role = :canonical WHERE role = :legacy"),
                {"canonical": canonical.value, "legacy": legacy.value},
            ).rowcount
        db.commit()
    return updated


def migrate_directory_org_ids() -> None:
    """Migrate existing directory records to have organization_id."""
    from app.models import Organization, Warehouse, Agency, Vendor
    
    with SessionLocal() as db:
        # Get the first organization (default org for existing records)
        default_org = db.scalar(select(Organization).limit(1))
        if not default_org:
            return  # No organization exists yet, skip migration
        
        dialect = engine.dialect.name if engine is not None else "sqlite"
        org_id = str(default_org.id) if dialect == "postgresql" else default_org.id.hex

        warehouses_updated = db.execute(
            text("UPDATE warehouses SET organization_id = :org_id WHERE organization_id IS NULL"),
            {"org_id": org_id},
        ).rowcount
        agencies_updated = db.execute(
            text("UPDATE agencies SET organization_id = :org_id WHERE organization_id IS NULL"),
            {"org_id": org_id},
        ).rowcount
        vendors_updated = db.execute(
            text("UPDATE vendors SET organization_id = :org_id WHERE organization_id IS NULL"),
            {"org_id": org_id},
        ).rowcount
        
        db.commit()
        
        if warehouses_updated + agencies_updated + vendors_updated > 0:
            print(f"Migrated directory records: {warehouses_updated} warehouses, {agencies_updated} agencies, {vendors_updated} vendors")


# Additive columns only (nullable or defaulted). Never drop/recreate existing tables.
# Each entry is (sqlite_type, postgres_type). create_all still creates brand-new tables.
ADDITIVE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "assets": [
        ("current_custody_type", "VARCHAR(64)", "VARCHAR(64)"),
        ("carrier_name", "VARCHAR(255)", "VARCHAR(255)"),
        ("tracking_code", "VARCHAR(64)", "VARCHAR(64)"),
        ("current_status", "VARCHAR(64)", "VARCHAR(64)"),
        ("operational_status", "VARCHAR(64)", "VARCHAR(64)"),
        ("is_archived", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
        ("archived_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("warehouse_id", "CHAR(32)", "UUID"),
        ("agency_id", "CHAR(32)", "UUID"),
        ("telematics_provider", "VARCHAR(64)", "VARCHAR(64)"),
        ("telematics_device_id", "VARCHAR(255)", "VARCHAR(255)"),
        ("created_by_id", "CHAR(32)", "UUID"),
        ("updated_by_id", "CHAR(32)", "UUID"),
        ("current_odometer_miles", "NUMERIC(12,2)", "NUMERIC(12,2)"),
        ("current_engine_hours", "NUMERIC(10,2)", "NUMERIC(10,2)"),
        ("organization_id", "CHAR(32)", "UUID"),
    ],
    "deployments": [
        ("custody_type", "VARCHAR(64)", "VARCHAR(64)"),
        ("carrier_name", "VARCHAR(255)", "VARCHAR(255)"),
        ("tracking_code", "VARCHAR(64)", "VARCHAR(64)"),
        ("updated_by_id", "CHAR(32)", "UUID"),
        ("updated_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("end_reason", "VARCHAR(255)", "VARCHAR(255)"),
        ("completion_notes", "TEXT", "TEXT"),
        ("completed_by_id", "CHAR(32)", "UUID"),
        ("organization_id", "CHAR(32)", "UUID"),
        ("address", "VARCHAR(255)", "VARCHAR(255)"),
        ("latitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
        ("longitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
    ],
    "maintenance_work_orders": [
        ("organization_id", "CHAR(32)", "UUID"),
        ("inspection_id", "CHAR(32)", "UUID"),
        ("failed_component", "VARCHAR(64)", "VARCHAR(64)"),
        ("repair_channel", "VARCHAR(64) DEFAULT 'Internal'", "VARCHAR(64) DEFAULT 'Internal'"),
        ("vendor_name", "VARCHAR(255)", "VARCHAR(255)"),
        ("updated_by_id", "CHAR(32)", "UUID"),
        ("updated_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("inspection_item_id", "CHAR(32)", "UUID"),
        ("issue_source", "VARCHAR(64) DEFAULT 'Manual Report'", "VARCHAR(64) DEFAULT 'Manual Report'"),
        ("priority", "VARCHAR(64) DEFAULT 'medium'", "VARCHAR(64) DEFAULT 'medium'"),
        ("assigned_to_id", "CHAR(32)", "UUID"),
        ("investigation_notes", "TEXT", "TEXT"),
        ("repair_actions", "TEXT", "TEXT"),
        ("parts_used", "TEXT", "TEXT"),
        ("vendor_invoice_cost", "NUMERIC(12,2) DEFAULT 0", "NUMERIC(12,2) DEFAULT 0"),
        ("labor_hours", "NUMERIC(10,2) DEFAULT 0", "NUMERIC(10,2) DEFAULT 0"),
        ("downtime_start", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("downtime_end", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("root_cause_category", "VARCHAR(64)", "VARCHAR(64)"),
        ("root_cause_description", "TEXT", "TEXT"),
        ("corrective_action", "TEXT", "TEXT"),
        ("preventive_action", "TEXT", "TEXT"),
        ("completion_notes", "TEXT", "TEXT"),
        ("is_archived", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
    ],
    "inspections": [
        ("organization_id", "CHAR(32)", "UUID"),
        ("source", "VARCHAR(32)", "VARCHAR(32)"),
        ("updated_by_id", "CHAR(32)", "UUID"),
        ("updated_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
    ],
    "users": [
        ("organization_id", "CHAR(32)", "UUID"),
        ("last_login_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("password_reset_token", "VARCHAR(255)", "VARCHAR(255)"),
        ("password_reset_expires", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("password_changed_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
        ("updated_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
    ],
    "maintenance_schedules": [
        ("organization_id", "CHAR(32)", "UUID"),
    ],
    "warehouses": [
        ("organization_id", "CHAR(32)", "UUID"),
        ("city", "VARCHAR(255)", "VARCHAR(255)"),
        ("state", "VARCHAR(64)", "VARCHAR(64)"),
        ("zip_code", "VARCHAR(20)", "VARCHAR(20)"),
        ("country", "VARCHAR(64)", "VARCHAR(64)"),
        ("latitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
        ("longitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
    ],
    "agencies": [
        ("organization_id", "CHAR(32)", "UUID"),
        ("site_name", "VARCHAR(255)", "VARCHAR(255)"),
        ("address", "VARCHAR(255)", "VARCHAR(255)"),
        ("city", "VARCHAR(255)", "VARCHAR(255)"),
        ("state", "VARCHAR(64)", "VARCHAR(64)"),
        ("zip_code", "VARCHAR(20)", "VARCHAR(20)"),
        ("country", "VARCHAR(64)", "VARCHAR(64)"),
        ("latitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
        ("longitude", "NUMERIC(10,7)", "NUMERIC(10,7)"),
    ],
    "vendors": [
        ("organization_id", "CHAR(32)", "UUID"),
    ],
    "inventory_transactions": [
        ("reverses_transaction_id", "CHAR(32)", "UUID"),
        ("reversal_reason", "TEXT", "TEXT"),
    ],
}


def postgres_add_column_sql(table: str, column: str, col_type: str) -> str:
    return f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {col_type}'


def apply_additive_columns(bind=None) -> list[tuple[str, str]]:
    """Add missing model columns on existing tables. Safe to run on every startup."""
    bind = bind or engine
    dialect = bind.dialect.name
    added: list[tuple[str, str]] = []
    with bind.begin() as conn:
        if dialect == "postgresql":
            existing_tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()")
                )
            }
        else:
            existing_tables = {
                row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            }
        for table, columns in ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            if dialect == "postgresql":
                present = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = current_schema() AND table_name = :table"
                        ),
                        {"table": table},
                    )
                }
            else:
                present = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            for name, sqlite_type, postgres_type in columns:
                if name in present:
                    continue
                if dialect == "postgresql":
                    conn.execute(text(postgres_add_column_sql(table, name, postgres_type)))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqlite_type}"))
                added.append((table, name))
    return added


def ensure_schema() -> None:
    """create_all for new tables; ALTER existing tables for columns added after first deploy."""
    Base.metadata.create_all(bind=engine)
    apply_additive_columns(engine)


def additive_column_names(table: str) -> set[str]:
    return {name for name, _sqlite, _pg in ADDITIVE_COLUMNS.get(table, [])}


def model_columns_missing_migrations() -> list[tuple[str, str]]:
    """Model columns on upgraded tables that are not covered by ADDITIVE_COLUMNS or the original snapshot."""
    import app.models  # noqa: F401 — register metadata

    missing: list[tuple[str, str]] = []
    for table_name, original in ORIGINAL_TABLE_COLUMNS.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        covered = original | additive_column_names(table_name)
        for column in table.columns:
            if column.name not in covered:
                missing.append((table_name, column.name))
    return missing


# First-deploy columns for tables that later grew via ADDITIVE_COLUMNS.
# When you add a SQLAlchemy column to one of these tables, also add it to ADDITIVE_COLUMNS.
ORIGINAL_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "assets": frozenset({
        "id", "vin", "make_model", "initial_purchase_cost", "current_location",
        "asset_type", "created_at", "updated_at",
    }),
    "deployments": frozenset({
        "id", "asset_id", "location", "status", "started_at", "ended_at",
        "notes", "created_by_id", "created_at",
    }),
    "maintenance_work_orders": frozenset({
        "id", "asset_id", "title", "description", "status", "opened_at", "closed_at",
        "downtime_hours", "labor_cost", "parts_cost", "created_by_id", "created_at",
    }),
    "inspections": frozenset({
        "id", "asset_id", "status", "started_at", "submitted_at", "notes",
        "created_by_id", "created_at",
    }),
    "users": frozenset({
        "id", "email", "hashed_password", "full_name", "role", "is_active", "created_at",
    }),
    "maintenance_schedules": frozenset({
        "id", "asset_id", "name", "description", "interval_miles", "interval_engine_hours",
        "interval_days", "interval_months", "due_soon_threshold_miles", "due_soon_threshold_days",
        "auto_create_work_order", "is_active", "last_completed_date", "last_completed_engine_hours",
        "last_completed_miles", "pending_work_order_id", "created_by_id", "created_at", "updated_at",
    }),
    "warehouses": frozenset({
        "id", "name", "address", "is_archived", "created_at", "updated_at", "created_by_id",
    }),
    "agencies": frozenset({
        "id", "name", "agency_type", "contact_name", "is_archived", "created_at", "updated_at",
        "created_by_id", "updated_by_id",
    }),
    "vendors": frozenset({
        "id", "name", "specialty", "is_archived", "created_at", "updated_at", "created_by_id", "updated_by_id",
    }),
    "inventory_transactions": frozenset({
        "id", "organization_id", "part_id", "work_order_id", "asset_id", "txn_type",
        "quantity_delta", "quantity_after", "unit_cost", "allow_negative", "notes",
        "created_by_id", "created_at",
    }),
}
