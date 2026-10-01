"""In-app notifications, email fan-out, and browser push registration.

Three delivery channels, deliberately independent:

* in-app rows, which are the source of truth and always written;
* email, which is best-effort and logged on failure;
* Web Push, which reaches a browser whose tab is not focused.

Only the first is required for a notification to count as delivered, so a
misconfigured mail provider or a browser that never granted permission cannot
lose the fact that a request arrived.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.email import send_email
from app.models import Notification, PushSubscription, User, UserRole
from app.rbac import canonical_role

logger = logging.getLogger("fleet.notifications")

router = APIRouter(tags=["notifications"])

# Canonical roles only; legacy spellings resolve through canonical_role().
NOTIFY_ROLES = frozenset({UserRole.SYSTEM_ADMIN, UserRole.ORG_ADMIN, UserRole.FLEET_MANAGER})

MAX_FEED = 100


def notifiable_users(db: Session, organization_id: UUID) -> list[User]:
    """Active Fleet Admin / Fleet Manager accounts in one organization.

    Filtered in Python on canonical_role so a legacy role value such as
    "dispatcher" still resolves to FLEET_MANAGER and gets notified.
    """
    users = db.scalars(
        select(User)
        .where(User.organization_id == organization_id)
        .where(User.is_active.is_(True))
    ).all()
    return [user for user in users if canonical_role(user) in NOTIFY_ROLES]


def serialize_notification(row: Notification) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "link_path": row.link_path,
        "read_at": row.read_at,
        "created_at": row.created_at,
    }


def notify_roles(
    db: Session,
    organization_id: UUID,
    *,
    kind: str,
    title: str,
    body: str,
    link_path: str | None = None,
    email_subject: str | None = None,
    email_body: str | None = None,
    reply_to: str | None = None,
) -> list[Notification]:
    """Write an in-app notification per recipient, then try email and push.

    Returns the rows written. The caller is expected to commit; the rows are
    added to the caller's session so a notification cannot survive a
    transaction that rolled back.
    """
    recipients = notifiable_users(db, organization_id)
    rows: list[Notification] = []
    for user in recipients:
        row = Notification(
            organization_id=organization_id,
            user_id=user.id,
            kind=kind,
            title=title,
            body=body,
            link_path=link_path,
        )
        db.add(row)
        rows.append(row)

    if not recipients:
        logger.warning(
            "No Fleet Admin or Fleet Manager in organization %s to notify about %r.",
            organization_id,
            title,
        )

    _send_notification_email(recipients, email_subject or title, email_body or body, reply_to)
    _send_push(db, [user.id for user in recipients], title, body, link_path)
    return rows


def clear_notifications(db: Session, organization_id: UUID, link_path: str) -> int:
    """Drop every recipient's copy of a notification once it no longer needs action.

    Deliberately org-wide rather than per-user: one manager approving a request
    settles it for the whole team, so leaving it unread in everyone else's bell
    would just be asking four people to dismiss the same decision.

    Matched on link_path because that is what makes a notification point at a
    request, and it is generated in one place rather than typed by hand. The
    caller is expected to commit.
    """
    rows = db.scalars(
        select(Notification)
        .where(Notification.organization_id == organization_id)
        .where(Notification.link_path == link_path)
    ).all()
    for row in rows:
        db.delete(row)
    return len(rows)


def _send_notification_email(
    recipients: list[User], subject: str, body: str, reply_to: str | None
) -> None:
    settings = get_settings()
    # An explicit recipient list wins, so alerts can be routed to a shared
    # mailbox without giving that mailbox a Fleet Command account.
    addresses = settings.email_notify_recipient_list or [user.email for user in recipients]
    if not addresses:
        return
    # Failure is logged inside send_email and never propagates: the request the
    # notification is about has already been accepted.
    send_email(addresses, subject, body, reply_to=reply_to)


def _send_push(
    db: Session, user_ids: list[UUID], title: str, body: str, link_path: str | None
) -> None:
    """Deliver Web Push to every registered browser for these users.

    Requires VAPID keys and the optional `pywebpush` dependency. Without either,
    push is skipped silently — the in-app row and the email already landed.
    """
    settings = get_settings()
    if not settings.push_enabled or not user_ids:
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.info("pywebpush is not installed; browser push notifications are skipped.")
        return

    subscriptions = db.scalars(
        select(PushSubscription).where(PushSubscription.user_id.in_(user_ids))
    ).all()
    payload = json.dumps({"title": title, "body": body, "link_path": link_path})
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
        except WebPushException as exc:
            # 404/410 means the browser dropped the subscription; drop ours too
            # so a dead endpoint is not retried forever.
            gone = exc.response is not None and exc.response.status_code in {404, 410}
            if gone:
                db.delete(subscription)
            logger.info("Push delivery failed for subscription %s: %s", subscription.id, exc)
        except Exception:
            logger.exception("Unexpected push failure for subscription %s", subscription.id)


# --- API -------------------------------------------------------------------


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=512)
    keys: PushKeys


@router.get("/notifications")
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=MAX_FEED),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The caller's own feed. A notification is never visible to anyone else."""
    stmt = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread_count = db.scalar(
        select(func.count(Notification.id))
        .where(Notification.user_id == current_user.id)
        .where(Notification.read_at.is_(None))
    )
    return {
        "unread_count": unread_count,
        "items": [serialize_notification(row) for row in rows],
    }


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(Notification)
        .where(Notification.id == notification_id)
        .where(Notification.user_id == current_user.id)
    )
    if row is None:
        # 404 rather than 403: another user's notification should not be
        # confirmed to exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.commit()
    return serialize_notification(row)


@router.post("/notifications/read-all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .where(Notification.read_at.is_(None))
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    db.commit()
    return {"marked": len(rows)}


@router.get("/notifications/push/key")
def get_push_key(current_user: User = Depends(get_current_user)) -> dict:
    """The VAPID public key. Public by design; the private key never leaves."""
    settings = get_settings()
    return {"enabled": settings.push_enabled, "public_key": settings.vapid_public_key}


@router.post("/notifications/push/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe_push(
    payload: PushSubscriptionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if existing is not None:
        # Re-subscribing the same browser reassigns it rather than duplicating,
        # which also covers a shared machine changing hands.
        existing.user_id = current_user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
    else:
        db.add(
            PushSubscription(
                user_id=current_user.id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
            )
        )
    db.commit()
    return {"subscribed": True}


@router.delete("/notifications/push/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_push(
    endpoint: str = Query(max_length=512),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    row = db.scalar(
        select(PushSubscription)
        .where(PushSubscription.endpoint == endpoint)
        .where(PushSubscription.user_id == current_user.id)
    )
    if row is not None:
        db.delete(row)
        db.commit()
