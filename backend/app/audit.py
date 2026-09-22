"""Audit logging utilities."""

import json
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log_audit(
    db: Session,
    user: User,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Create an audit log entry."""
    audit = AuditLog(
        organization_id=user.organization_id,
        user_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=json.dumps(details) if details else None,
        ip_address=ip_address,
    )
    db.add(audit)
    db.commit()
    return audit


# Audit action constants
class AuditAction:
    # Asset actions
    ASSET_CREATED = "asset_created"
    ASSET_UPDATED = "asset_updated"
    ASSET_ARCHIVED = "asset_archived"
    ASSET_DELETED = "asset_deleted"
    
    # Deployment actions
    DEPLOYMENT_STARTED = "deployment_started"
    DEPLOYMENT_ENDED = "deployment_ended"
    DEPLOYMENT_UPDATED = "deployment_updated"
    CUSTODY_TRANSFERRED = "custody_transferred"
    
    # Work order actions
    WORK_ORDER_CREATED = "work_order_created"
    WORK_ORDER_UPDATED = "work_order_updated"
    WORK_ORDER_CLOSED = "work_order_closed"
    WORK_ORDER_DELETED = "work_order_deleted"
    WORK_ORDER_COST_ADDED = "work_order_cost_added"
    
    # Inspection actions
    INSPECTION_STARTED = "inspection_started"
    INSPECTION_SUBMITTED = "inspection_submitted"
    INSPECTION_ITEM_UPDATED = "inspection_item_updated"
    
    # User management actions
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DISABLED = "user_disabled"
    USER_ENABLED = "user_enabled"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_PASSWORD_RESET = "user_password_reset"
    
    # Authorization actions
    ASSET_AUTHORIZATION_GRANTED = "asset_authorization_granted"
    ASSET_AUTHORIZATION_REVOKED = "asset_authorization_revoked"
    
    # Organization actions
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_UPDATED = "organization_updated"
