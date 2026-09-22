"""Additive schema migrations stay idempotent and include Deployment map columns."""

from sqlalchemy import create_engine, text

from app.database import ADDITIVE_COLUMNS, apply_additive_columns, model_columns_missing_migrations, postgres_add_column_sql


def test_deployment_map_columns_are_registered_for_postgres():
    names = {name for name, _sqlite, _pg in ADDITIVE_COLUMNS["deployments"]}
    assert {"address", "latitude", "longitude"} <= names
    assert postgres_add_column_sql("deployments", "address", "VARCHAR(255)") == (
        'ALTER TABLE "deployments" ADD COLUMN IF NOT EXISTS "address" VARCHAR(255)'
    )


def test_additive_columns_fill_missing_deployment_fields_and_are_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE deployments (
                    id CHAR(32) PRIMARY KEY,
                    location VARCHAR(255) NOT NULL
                )
                """
            )
        )

    first = apply_additive_columns(engine)
    added_names = {name for table, name in first if table == "deployments"}
    assert {"address", "latitude", "longitude"} <= added_names

    with engine.begin() as conn:
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(deployments)")).fetchall()}
    assert {"address", "latitude", "longitude", "location"} <= present

    second = apply_additive_columns(engine)
    assert second == []


def test_model_columns_have_additive_coverage():
    missing = model_columns_missing_migrations()
    assert missing == [], f"Add these columns to ADDITIVE_COLUMNS: {missing}"
