"""Role-Based Access Control and Organization-level authorization.

This module is dependency-free with respect to FastAPI request handling so that
it can be imported from anywhere without a circular import. The request-scoped
dependencies that wrap these rules live in `app.deps`.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.models import Asset, AssetAuthorization, Deployment, Inspection, MaintenanceSchedule, MaintenanceWorkOrder, User, UserRole

# Legacy role values still present in existing databases. Every authorization
# decision resolves through canonical_role() so a legacy value can never be
# missed by a role check that only lists the modern spelling.
CANONICAL_ROLE: dict[UserRole, UserRole] = {
    UserRole.ADMIN: UserRole.ORG_ADMIN,
    UserRole.DISPATCHER: UserRole.FLEET_MANAGER,
    UserRole.PROGRAM_MANAGER: UserRole.FLEET_MANAGER,
    UserRole.VIEWER: UserRole.READ_ONLY,
}

LEGACY_ROLES = frozenset(CANONICAL_ROLE)


def canonical_role(subject: User | UserRole) -> UserRole:
    """Resolve a user or role to its modern equivalent."""
    role = subject.role if isinstance(subject, User) else subject
    return CANONICAL_ROLE.get(role, role)


# Privilege ordering. A user may only grant a role at or below their own rank,
# which is what stops an org admin from minting a cross-tenant system admin.
ROLE_RANK: dict[UserRole, int] = {
    UserRole.SYSTEM_ADMIN: 100,
    UserRole.ORG_ADMIN: 80,
    UserRole.FLEET_MANAGER: 60,
    UserRole.TECHNICIAN: 40,
    UserRole.READ_ONLY: 20,
    UserRole.CUSTOMER: 10,
}


def role_rank(subject: User | UserRole) -> int:
    return ROLE_RANK.get(canonical_role(subject), 0)


ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.SYSTEM_ADMIN: frozenset({
        "view_all_organizations",
        "manage_organizations",
        "manage_users",
        "manage_org_users",
        "view_assets",
        "manage_assets",
        "view_maintenance",
        "manage_maintenance",
        "view_analytics",
        "export_data",
        "audit_logs",
    }),
    UserRole.ORG_ADMIN: frozenset({
        "manage_org_users",
        "view_assets",
        "manage_assets",
        "view_maintenance",
        "manage_maintenance",
        "view_analytics",
        "export_data",
    }),
    UserRole.FLEET_MANAGER: frozenset({
        "view_assets",
        "manage_assets",
        "view_maintenance",
        "manage_maintenance",
        "view_analytics",
        "export_data",
    }),
    UserRole.TECHNICIAN: frozenset({
        "view_assets",
        "view_maintenance",
        "manage_maintenance",
    }),
    UserRole.READ_ONLY: frozenset({
        "view_assets",
        "view_maintenance",
        "view_analytics",
    }),
    UserRole.CUSTOMER: frozenset({
        "view_authorized_assets",
    }),
}

# Roles that may only read. Used to gate every write endpoint.
READ_ONLY_ROLES = frozenset({UserRole.READ_ONLY, UserRole.CUSTOMER})


def user_has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(canonical_role(user), frozenset())


def can_assign_role(actor: User, target_role: UserRole) -> bool:
    """A user may only create or promote accounts at or below their own rank."""
    return role_rank(actor) >= role_rank(target_role)


def require_assignable_role(actor: User, target_role: UserRole) -> None:
    if not can_assign_role(actor, target_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You cannot assign the '{canonical_role(target_role).value}' role.",
        )


def is_system_admin(user: User) -> bool:
    return canonical_role(user) == UserRole.SYSTEM_ADMIN


def is_customer(user: User) -> bool:
    return canonical_role(user) == UserRole.CUSTOMER


def can_access_organization(user: User, org_id: UUID) -> bool:
    """Check if user can access data for a specific organization."""
    if is_system_admin(user):
        return True
    return user.organization_id == org_id


def require_org_access(org_id: UUID, user: User) -> None:
    """Raise 403 if user cannot access the organization."""
    if not can_access_organization(user, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to this organization's data is forbidden",
        )


def customer_agency_id(user: User) -> UUID | None:
    """The single agency a customer account is scoped to, if one is assigned."""
    return user.agency_id if is_customer(user) else None


def get_user_asset_ids(db: Session, user: User) -> set[UUID] | None:
    """Asset IDs a user may see, or None when they may see their whole organization.

    For a customer this is the intersection of three scopes: their organization,
    the one agency their account belongs to, and the assets explicitly granted
    to them. Joining Asset means the agency is read live, so an asset that moves
    to another agency drops out immediately even though the grant row survives.

    A customer with no agency assigned resolves to the empty set. They must never
    fall back to "all customer assets".
    """
    if not is_customer(user):
        return None
    if user.agency_id is None:
        return set()
    return set(
        db.scalars(
            select(AssetAuthorization.asset_id)
            .join(Asset, Asset.id == AssetAuthorization.asset_id)
            .where(AssetAuthorization.user_id == user.id)
            .where(AssetAuthorization.can_view.is_(True))
            .where(Asset.organization_id == user.organization_id)
            .where(Asset.agency_id == user.agency_id)
        ).all()
    )


def filter_by_authorized_assets(query, user: User, db: Session, asset_id_column):
    """Restrict a query to assets the current user may view.

    Customers: only AssetAuthorization rows with can_view=True.
    Other roles: no extra asset filter (caller applies organization scope).
    """
    if not is_customer(user):
        return query
    authorized_asset_ids = get_user_asset_ids(db, user)
    if not authorized_asset_ids:
        return query.where(false())
    return query.where(asset_id_column.in_(authorized_asset_ids))


def filter_assets_by_access(query, user: User, db: Session, *, organization_id: UUID | None = None):
    """Apply asset access filter to a query based on user role and authorizations.

    Every caller gets an organization scope. System admins default to their own
    organization unless an explicit organization_id is supplied, so that no
    endpoint silently returns cross-tenant rows.
    """
    if is_customer(user):
        authorized_asset_ids = get_user_asset_ids(db, user)
        if not authorized_asset_ids:
            return query.where(false())
        # The org and agency predicates are redundant with the id set above, and
        # deliberately so: the scope stays enforced in the SQL even if a caller
        # ever supplies asset ids from somewhere else.
        return (
            query.where(Asset.organization_id == user.organization_id)
            .where(Asset.agency_id == user.agency_id)
            .where(Asset.id.in_(authorized_asset_ids))
        )

    scope = organization_id if organization_id is not None else user.organization_id
    return query.where(Asset.organization_id == scope)


def require_asset_access(db: Session, user: User, asset_id: UUID) -> Asset:
    """Return the asset if the user may see it, otherwise raise 404.

    404 rather than 403 is deliberate: a cross-tenant caller should not be able
    to confirm that an asset ID exists.
    """
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    if is_system_admin(user):
        return asset

    if is_customer(user):
        # Same three scopes as get_user_asset_ids, and the same 404 for each, so
        # a customer cannot tell an out-of-agency asset from a nonexistent one.
        if (
            user.agency_id is None
            or asset.organization_id != user.organization_id
            or asset.agency_id != user.agency_id
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
        auth = db.scalar(
            select(AssetAuthorization)
            .where(AssetAuthorization.user_id == user.id)
            .where(AssetAuthorization.asset_id == asset_id)
            .where(AssetAuthorization.can_view.is_(True))
        )
        if not auth:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
        return asset

    if asset.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    return asset


def _require_via_asset(db: Session, user: User, model, record_id: UUID, label: str):
    record = db.get(model, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    require_asset_access(db, user, record.asset_id)
    return record


def require_inspection_access(db: Session, user: User, inspection_id: UUID) -> Inspection:
    return _require_via_asset(db, user, Inspection, inspection_id, "Inspection")


def require_work_order_access(db: Session, user: User, wo_id: UUID) -> MaintenanceWorkOrder:
    return _require_via_asset(db, user, MaintenanceWorkOrder, wo_id, "Work order")


def require_pm_schedule_access(db: Session, user: User, schedule_id: UUID) -> MaintenanceSchedule:
    return _require_via_asset(db, user, MaintenanceSchedule, schedule_id, "PM schedule")


def require_deployment_access(db: Session, user: User, deployment_id: UUID) -> Deployment:
    return _require_via_asset(db, user, Deployment, deployment_id, "Deployment")
