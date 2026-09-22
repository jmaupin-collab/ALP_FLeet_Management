"""Minimal seed data for testing authentication - bypasses multi-tenancy requirements"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Organization, User, UserRole


def seed_minimal(db: Session) -> None:
    """Create minimal seed data: one organization and two users"""
    if db.scalar(select(User).limit(1)):
        print("Database already seeded")
        return
    
    # Create default organization
    default_org = Organization(
        name="Internal Fleet Operations",
        slug="internal",
        org_type="internal",
        is_active=True,
    )
    db.add(default_org)
    db.flush()
    
    # Create admin user
    admin = User(
        organization_id=default_org.id,
        email="admin@example.com",
        hashed_password=hash_password("ChangeMe123!"),
        full_name="Avery Chen",
        role=UserRole.ORG_ADMIN,
    )
    
    # Create tech user
    tech = User(
        organization_id=default_org.id,
        email="tech@example.com",
        hashed_password=hash_password("ChangeMe123!"),
        full_name="Marcus Hale",
        role=UserRole.TECHNICIAN,
    )
    
    db.add_all([admin, tech])
    db.commit()
    
    print(f"✓ Created organization: {default_org.name}")
    print(f"✓ Created admin user: {admin.email}")
    print(f"✓ Created tech user: {tech.email}")
    print("\nLogin credentials:")
    print("  Email: admin@example.com")
    print("  Password: ChangeMe123!")
