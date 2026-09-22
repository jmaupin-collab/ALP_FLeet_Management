"""Create the first administrator account interactively.

Demo seeding is off by default and refused in production, so this is the
supported way to bootstrap a real deployment. Safe to re-run: it updates the
password of an existing account rather than creating a duplicate.

    python -m scripts.create_admin
    python -m scripts.create_admin --email ops@example.com --role system_admin

For unattended provisioning, set FLEET_ADMIN_PASSWORD instead of typing one.
It is read from the environment rather than a flag so the password does not
land in shell history or the process list.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.database import SessionLocal, ensure_schema  # noqa: E402
from app.models import Organization, User, UserRole  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def prompt_password() -> str:
    from_env = os.environ.get("FLEET_ADMIN_PASSWORD")
    if from_env:
        if len(from_env) < MIN_PASSWORD_LENGTH:
            raise SystemExit(f"FLEET_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters.")
        return from_env

    while True:
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            continue
        if password != getpass.getpass("Confirm password: "):
            print("Passwords did not match.")
            continue
        return password


def resolve_organization(db, slug: str) -> Organization:
    org = db.scalar(select(Organization).where(Organization.slug == slug))
    if org:
        return org
    org = Organization(name="Internal Fleet Operations", slug=slug, org_type="internal", is_active=True)
    db.add(org)
    db.flush()
    print(f"Created organization '{org.name}' ({slug}).")
    return org


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset an administrator account.")
    parser.add_argument("--email")
    parser.add_argument("--full-name", default=None)
    parser.add_argument(
        "--role",
        default=UserRole.SYSTEM_ADMIN.value,
        choices=[UserRole.SYSTEM_ADMIN.value, UserRole.ORG_ADMIN.value],
    )
    parser.add_argument("--org-slug", default="internal")
    args = parser.parse_args()

    ensure_schema()

    email = (args.email or input("Email: ")).strip().lower()
    if not email:
        print("An email address is required.", file=sys.stderr)
        return 1

    password = prompt_password()
    full_name = args.full_name or input("Full name: ").strip() or email

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(func.lower(User.email) == email))
        if existing:
            existing.hashed_password = hash_password(password)
            existing.role = UserRole(args.role)
            existing.is_active = True
            existing.password_changed_at = datetime.now(UTC)
            db.commit()
            print(f"Updated existing account {email} ({args.role}).")
            return 0

        org = resolve_organization(db, args.org_slug)
        db.add(
            User(
                organization_id=org.id,
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
                role=UserRole(args.role),
                is_active=True,
                password_changed_at=datetime.now(UTC),
            )
        )
        db.commit()
        print(f"Created {args.role} account {email}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
