"""Request-scoped FastAPI dependencies.

Sits above `app.auth` (token mechanics) and `app.rbac` (authorization rules) so
neither of those needs to import the other. Route modules import from here.
"""

from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.database import get_db
from app.models import User, UserRole
from app.rbac import READ_ONLY_ROLES, canonical_role, user_has_permission

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHENTICATED

    try:
        payload = decode_access_token(creds.credentials)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or missing user")

    # A password change invalidates every token issued before it. `iat` is
    # floored to whole seconds, so a token minted in the same second as the
    # change is treated as older and rejected — erring toward re-authentication.
    changed_at = user.password_changed_at
    issued_at = payload.get("iat")
    if changed_at is not None and issued_at is not None:
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=UTC)
        if datetime.fromtimestamp(issued_at, tz=UTC) < changed_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired. Sign in again.",
            )

    return user


def require_permission(permission: str):
    """Dependency factory gating an endpoint on a single named permission."""

    def check_permission(user: User = Depends(get_current_user)) -> User:
        if not user_has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return user

    return check_permission


def _require_roles(allowed: frozenset[UserRole], message: str):
    def check(current_user: User = Depends(get_current_user)) -> User:
        if canonical_role(current_user) not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
        return current_user

    return check


def require_operator(current_user: User = Depends(get_current_user)) -> User:
    """Operators can create/edit data (not just view)."""
    if canonical_role(current_user) in READ_ONLY_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return current_user


require_reporter = _require_roles(
    frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER}),
    "Reporter role required",
)

require_fleet_admin = _require_roles(
    frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER}),
    "Fleet admin role required",
)

require_admin = _require_roles(
    frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN}),
    "Admin role required",
)

require_system_admin = _require_roles(
    frozenset({UserRole.SYSTEM_ADMIN}),
    "System admin role required",
)
