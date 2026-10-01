"""Public ALPR request intake and the internal Request Center.

Two surfaces with very different trust levels in one module, because the
boundary between them is the whole point and is easier to audit in one place.

The public half accepts an intake record and nothing more. It cannot assign an
asset, open a deployment, change custody, change an asset's status, or move
anything on the map. Turning a request into fleet movement is a separate,
authenticated action that calls the existing lifecycle services rather than
reimplementing them.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.captcha import get_verifier
from app.config import get_settings
from app.database import get_db
from app.deps import require_fleet_admin
from app.models import (
    OPERATIONAL_ASSET_TYPE,
    Agency,
    AlprRequest,
    AlprRequestAsset,
    AlprRequestEvent,
    AlprRequestStatus,
    AlprRequestType,
    Asset,
    Organization,
    User,
    Warehouse,
)
from app.notifications import clear_notifications, notify_roles
from app.ratelimit import check_rate_limit, client_ip
from app.request_schemas import (
    AlprRequestAssetsSet,
    AlprRequestFulfill,
    AlprRequestOut,
    AlprRequestSchedule,
    AlprRequestStatusChange,
    AlprRequestUpdate,
    PublicAlprRequestAccepted,
    PublicAlprRequestCreate,
    PublicFormConfig,
)

logger = logging.getLogger("fleet.requests")

public_router = APIRouter(prefix="/public", tags=["public-requests"])
router = APIRouter(tags=["requests"])

# Which status may follow which. Anything not listed is refused, so a request
# cannot skip review or come back from a terminal state by accident.
ALLOWED_TRANSITIONS: dict[AlprRequestStatus, frozenset[AlprRequestStatus]] = {
    AlprRequestStatus.NEW: frozenset(
        {AlprRequestStatus.UNDER_REVIEW, AlprRequestStatus.APPROVED,
         AlprRequestStatus.REJECTED, AlprRequestStatus.CANCELLED}
    ),
    AlprRequestStatus.UNDER_REVIEW: frozenset(
        {AlprRequestStatus.APPROVED, AlprRequestStatus.REJECTED, AlprRequestStatus.CANCELLED}
    ),
    AlprRequestStatus.APPROVED: frozenset(
        {AlprRequestStatus.SCHEDULED, AlprRequestStatus.IN_PROGRESS,
         AlprRequestStatus.UNDER_REVIEW, AlprRequestStatus.CANCELLED}
    ),
    AlprRequestStatus.SCHEDULED: frozenset(
        {AlprRequestStatus.IN_PROGRESS, AlprRequestStatus.APPROVED, AlprRequestStatus.CANCELLED}
    ),
    AlprRequestStatus.IN_PROGRESS: frozenset(
        {AlprRequestStatus.COMPLETED, AlprRequestStatus.SCHEDULED, AlprRequestStatus.CANCELLED}
    ),
    # Terminal.
    AlprRequestStatus.COMPLETED: frozenset(),
    AlprRequestStatus.REJECTED: frozenset(),
    AlprRequestStatus.CANCELLED: frozenset(),
}

# A request has to be approved before any trailer can be committed to it.
FULFILLABLE_STATUSES = frozenset(
    {AlprRequestStatus.APPROVED, AlprRequestStatus.SCHEDULED, AlprRequestStatus.IN_PROGRESS}
)

# Once someone has made the call, the "new request" notification has done its
# job and is cleared from every recipient's bell. Under Review is missing on
# purpose: it means a person picked the request up, not that it is settled.
DECIDED_STATUSES = frozenset(
    {
        AlprRequestStatus.APPROVED,
        AlprRequestStatus.REJECTED,
        AlprRequestStatus.CANCELLED,
        AlprRequestStatus.COMPLETED,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def _reference() -> str:
    """Short, unguessable, human-readable handle. Not a security boundary."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
    return "ALPR-" + "".join(secrets.choice(alphabet) for _ in range(6))


def _unique_reference(db: Session) -> str:
    for _ in range(10):
        candidate = _reference()
        if db.scalar(select(AlprRequest.id).where(AlprRequest.reference == candidate)) is None:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not allocate a request reference. Try again.",
    )


def resolve_public_organization(db: Session) -> Organization:
    """Which tenant owns a request that arrived with no logged-in user.

    An explicit PUBLIC_REQUEST_ORG_SLUG wins. Otherwise the single internal
    organization is used, and an ambiguous multi-tenant install is refused
    rather than guessed at — routing a customer's request to the wrong tenant
    would be a data leak.
    """
    settings = get_settings()
    if settings.public_request_org_slug:
        org = db.scalar(
            select(Organization).where(Organization.slug == settings.public_request_org_slug)
        )
        if org is None:
            logger.error(
                "PUBLIC_REQUEST_ORG_SLUG=%r does not match any organization.",
                settings.public_request_org_slug,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Public requests are not configured. Contact the fleet team directly.",
            )
        return org

    internal = db.scalars(
        select(Organization)
        .where(Organization.org_type == "internal")
        .where(Organization.is_active.is_(True))
        .order_by(Organization.created_at)
    ).all()
    if len(internal) == 1:
        return internal[0]
    logger.error(
        "Cannot route a public request: %d active internal organizations found. "
        "Set PUBLIC_REQUEST_ORG_SLUG.",
        len(internal),
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Public requests are not configured. Contact the fleet team directly.",
    )


def _record_event(
    db: Session,
    request_row: AlprRequest,
    *,
    event_type: str,
    message: str | None = None,
    from_status: AlprRequestStatus | None = None,
    to_status: AlprRequestStatus | None = None,
    actor_id: UUID | None = None,
) -> AlprRequestEvent:
    event = AlprRequestEvent(
        request_id=request_row.id,
        event_type=event_type,
        message=message,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
    )
    db.add(event)
    return event


def _serialize(db: Session, row: AlprRequest) -> AlprRequestOut:
    actor_names = {}
    actor_ids = {event.actor_id for event in row.events if event.actor_id}
    if actor_ids:
        actor_names = {
            user.id: user.full_name
            for user in db.scalars(select(User).where(User.id.in_(actor_ids))).all()
        }
    return AlprRequestOut(
        id=row.id,
        reference=row.reference,
        organization_id=row.organization_id,
        request_type=row.request_type,
        status=row.status,
        agency_name=row.agency_name,
        agency_id=row.agency_id,
        requester_name=row.requester_name,
        requester_email=row.requester_email,
        requester_phone=row.requester_phone,
        requested_date=row.requested_date,
        address=row.address,
        latitude=row.latitude,
        longitude=row.longitude,
        quantity=row.quantity,
        notes=row.notes,
        review_notes=row.review_notes,
        scheduled_date=row.scheduled_date,
        reviewed_by_id=row.reviewed_by_id,
        reviewed_at=row.reviewed_at,
        completed_at=row.completed_at,
        completed_by_id=row.completed_by_id,
        completion_notes=row.completion_notes,
        source_ip=row.source_ip,
        captcha_verified=row.captcha_verified,
        created_at=row.created_at,
        updated_at=row.updated_at,
        assets=[
            {
                "id": link.id,
                "asset_id": link.asset_id,
                "vin": link.asset.vin,
                "make_model": link.asset.make_model,
                "license_plate": link.asset.license_plate,
                "operational_status": (
                    link.asset.operational_status.value if link.asset.operational_status else None
                ),
                "fulfilled_at": link.fulfilled_at,
            }
            for link in row.assets
        ],
        events=[
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "message": event.message,
                "actor_id": event.actor_id,
                "actor_name": actor_names.get(event.actor_id),
                "created_at": event.created_at,
            }
            for event in row.events
        ],
    )


def _load(db: Session, request_id: UUID, organization_id: UUID) -> AlprRequest:
    """Load one request inside the caller's tenant.

    A request belonging to another organization returns 404, not 403, so its
    existence is never confirmed across a tenant boundary.
    """
    row = db.scalar(
        select(AlprRequest)
        .options(
            selectinload(AlprRequest.assets).selectinload(AlprRequestAsset.asset),
            selectinload(AlprRequest.events),
        )
        .where(AlprRequest.id == request_id)
        .where(AlprRequest.organization_id == organization_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return row


# --- Public surface ---------------------------------------------------------


@public_router.get("/alpr-requests/config", response_model=PublicFormConfig)
def public_form_config() -> PublicFormConfig:
    """Everything the anonymous form needs, and nothing about the fleet."""
    settings = get_settings()
    return PublicFormConfig(
        captcha_provider=settings.captcha_provider if settings.captcha_enabled else "none",
        captcha_site_key=settings.captcha_site_key if settings.captcha_enabled else None,
    )


@public_router.post(
    "/alpr-requests",
    response_model=PublicAlprRequestAccepted,
    status_code=status.HTTP_201_CREATED,
)
def submit_public_request(
    payload: PublicAlprRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> PublicAlprRequestAccepted:
    """Accept an intake record from an unauthenticated visitor.

    Creates exactly one AlprRequest row plus its submission event. It must not
    touch an asset, a deployment, custody, or the map — that separation is what
    makes an anonymous endpoint safe to expose.
    """
    settings = get_settings()
    ip = client_ip(request)

    allowed, retry_after = check_rate_limit(
        f"public-alpr-request:{ip}",
        settings.public_request_rate_limit,
        settings.public_request_rate_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests from this address. Try again later.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    verifier = get_verifier()
    if not verifier.verify(payload.captcha_token, ip):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not verify that you are human. Please retry the challenge.",
        )

    organization = resolve_public_organization(db)

    # Geocoding is a nicety. A failure leaves the coordinates null and the
    # typed address intact rather than losing the submission.
    latitude = longitude = None
    try:
        from app.geocoding import geocode_directory_address

        latitude, longitude = geocode_directory_address(payload.address, label="ALPR request")
    except Exception:
        logger.exception("Geocoding raised for a public request; storing the address without coordinates.")

    row = AlprRequest(
        organization_id=organization.id,
        reference=_unique_reference(db),
        request_type=payload.request_type,
        status=AlprRequestStatus.NEW,
        agency_name=payload.agency_name,
        requester_name=payload.requester_name,
        requester_email=str(payload.requester_email),
        requester_phone=payload.requester_phone,
        requested_date=payload.requested_date,
        address=payload.address,
        latitude=latitude,
        longitude=longitude,
        quantity=payload.quantity,
        notes=payload.notes,
        source_ip=ip,
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
        captcha_verified=verifier.enabled,
    )
    db.add(row)
    db.flush()

    _record_event(
        db,
        row,
        event_type="submitted",
        to_status=AlprRequestStatus.NEW,
        message=f"Submitted from the public form by {row.requester_name}.",
    )

    kind_label = "Deploy" if row.request_type == AlprRequestType.DEPLOY else "Pickup"
    notify_roles(
        db,
        organization.id,
        kind="alpr_request",
        title=f"New ALPR {kind_label.lower()} request · {row.agency_name}",
        body=(
            f"{row.requester_name} requested {row.quantity} trailer"
            f"{'' if row.quantity == 1 else 's'} ({row.reference})."
        ),
        link_path=f"/requests/{row.id}",
        email_subject=f"[Fleet Command] New ALPR {kind_label.lower()} request {row.reference}",
        email_body=(
            f"Reference: {row.reference}\n"
            f"Type: {kind_label}\n"
            f"Agency / customer: {row.agency_name}\n"
            f"Requester: {row.requester_name} <{row.requester_email}>"
            f"{f' · {row.requester_phone}' if row.requester_phone else ''}\n"
            f"Requested date: {row.requested_date or 'not specified'}\n"
            f"Quantity: {row.quantity}\n"
            f"Address: {row.address}\n\n"
            f"{row.notes or ''}\n\n"
            "Review it in the Request Center. No asset has been assigned and no "
            "deployment has been created."
        ),
        reply_to=row.requester_email,
    )

    db.commit()
    return PublicAlprRequestAccepted(
        reference=row.reference,
        status=AlprRequestStatus.NEW,
        message=(
            "Thanks — your request has been received. The fleet team will review it and "
            "contact you at the email address you provided."
        ),
    )


# --- Internal Request Center ------------------------------------------------


@router.get("/requests", response_model=list[AlprRequestOut])
def list_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    request_type: AlprRequestType | None = Query(default=None),
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> list[AlprRequestOut]:
    stmt = (
        select(AlprRequest)
        .options(
            selectinload(AlprRequest.assets).selectinload(AlprRequestAsset.asset),
            selectinload(AlprRequest.events),
        )
        .where(AlprRequest.organization_id == current_user.organization_id)
    )
    if status_filter:
        try:
            stmt = stmt.where(AlprRequest.status == AlprRequestStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown status {status_filter!r}.",
            )
    if request_type:
        stmt = stmt.where(AlprRequest.request_type == request_type)
    rows = db.scalars(stmt.order_by(AlprRequest.created_at.desc())).all()
    return [_serialize(db, row) for row in rows]


@router.get("/requests/summary")
def request_summary(
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Counts per status for the Request Center tabs."""
    rows = db.scalars(
        select(AlprRequest).where(AlprRequest.organization_id == current_user.organization_id)
    ).all()
    counts = {value.value: 0 for value in AlprRequestStatus}
    for row in rows:
        counts[row.status.value] += 1
    return {"total": len(rows), "counts": counts}


@router.get("/requests/{request_id}", response_model=AlprRequestOut)
def get_request(
    request_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    return _serialize(db, _load(db, request_id, current_user.organization_id))


@router.patch("/requests/{request_id}", response_model=AlprRequestOut)
def update_request(
    request_id: UUID,
    payload: AlprRequestUpdate,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    """Correct the operational details of an intake record.

    Editing never moves the status; that goes through the status endpoint so
    every transition is validated and recorded.
    """
    row = _load(db, request_id, current_user.organization_id)
    if row.status in {AlprRequestStatus.COMPLETED, AlprRequestStatus.REJECTED, AlprRequestStatus.CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {row.status.value} request can no longer be edited.",
        )

    data = payload.model_dump(exclude_unset=True)
    if "agency_id" in data and data["agency_id"] is not None:
        agency = db.scalar(
            select(Agency)
            .where(Agency.id == data["agency_id"])
            .where(Agency.organization_id == row.organization_id)
        )
        if agency is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agency not found")

    changed = []
    for key, value in data.items():
        if key == "requester_email" and value is not None:
            value = str(value)
        if getattr(row, key) != value:
            changed.append(key)
            setattr(row, key, value)

    # Re-geocode only when the address actually moved, and never let a failure
    # blank out coordinates that were already resolved.
    if "address" in data:
        from app.geocoding import geocode_directory_address

        row.latitude, row.longitude = geocode_directory_address(
            row.address, label="ALPR request", existing=(row.latitude, row.longitude)
        )

    if changed:
        _record_event(
            db,
            row,
            event_type="edited",
            message="Updated " + ", ".join(sorted(changed)) + ".",
            actor_id=current_user.id,
        )
        db.commit()
        db.refresh(row)
    return _serialize(db, row)


@router.post("/requests/{request_id}/status", response_model=AlprRequestOut)
def change_request_status(
    request_id: UUID,
    payload: AlprRequestStatusChange,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    """Move a request through review.

    Approving records a decision. It does not deploy anything: no asset is
    assigned and no custody changes until someone explicitly fulfils it.
    """
    row = _load(db, request_id, current_user.organization_id)
    target = payload.status
    if target == row.status:
        return _serialize(db, row)
    if target not in ALLOWED_TRANSITIONS[row.status]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {row.status.value} request cannot move to {target.value}.",
        )

    previous = row.status
    row.status = target
    if target in {
        AlprRequestStatus.UNDER_REVIEW,
        AlprRequestStatus.APPROVED,
        AlprRequestStatus.REJECTED,
    }:
        row.reviewed_by_id = current_user.id
        row.reviewed_at = _now()
    if target == AlprRequestStatus.COMPLETED:
        row.completed_at = _now()
        row.completed_by_id = current_user.id
        if payload.message:
            row.completion_notes = payload.message

    _record_event(
        db,
        row,
        event_type="status_changed",
        from_status=previous,
        to_status=target,
        message=payload.message,
        actor_id=current_user.id,
    )
    if target in DECIDED_STATUSES:
        clear_notifications(db, row.organization_id, f"/requests/{row.id}")
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.post("/requests/{request_id}/assets", response_model=AlprRequestOut)
def set_request_assets(
    request_id: UUID,
    payload: AlprRequestAssetsSet,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    """Pick the actual trailers for an approved request.

    Selecting is not deploying. The link records intent; custody only changes
    when the request is fulfilled through the lifecycle workflow.
    """
    row = _load(db, request_id, current_user.organization_id)
    if row.status not in FULFILLABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve the request before assigning trailers.",
        )

    requested = list(dict.fromkeys(payload.asset_ids))
    assets = (
        db.scalars(
            select(Asset)
            .where(Asset.id.in_(requested))
            .where(Asset.organization_id == row.organization_id)
        ).all()
        if requested
        else []
    )
    found = {asset.id for asset in assets}
    missing = [str(asset_id) for asset_id in requested if asset_id not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset not found: {', '.join(missing)}",
        )
    wrong_type = [asset.vin for asset in assets if asset.asset_type != OPERATIONAL_ASSET_TYPE]
    if wrong_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only ALPR Trailers can fulfil a request: {', '.join(wrong_type)}",
        )

    # Units already sent out for this request stay put; re-picking should not
    # silently detach something that has physically moved.
    locked = {link.asset_id for link in row.assets if link.fulfilled_at is not None}
    dropped = [link for link in row.assets if link.asset_id not in found and link.asset_id not in locked]
    for link in dropped:
        db.delete(link)
    existing = {link.asset_id for link in row.assets}
    for asset in assets:
        if asset.id not in existing:
            db.add(
                AlprRequestAsset(
                    request_id=row.id, asset_id=asset.id, created_by_id=current_user.id
                )
            )

    _record_event(
        db,
        row,
        event_type="assets_selected",
        message=(
            "Selected " + ", ".join(sorted(asset.vin for asset in assets))
            if assets
            else "Cleared the trailer selection."
        ),
        actor_id=current_user.id,
    )
    db.commit()
    return _serialize(db, _load(db, request_id, current_user.organization_id))


@router.post("/requests/{request_id}/schedule", response_model=AlprRequestOut)
def schedule_request(
    request_id: UUID,
    payload: AlprRequestSchedule,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    row = _load(db, request_id, current_user.organization_id)
    if row.status not in {AlprRequestStatus.APPROVED, AlprRequestStatus.SCHEDULED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve the request before scheduling it.",
        )
    previous = row.status
    row.scheduled_date = payload.scheduled_date
    row.status = AlprRequestStatus.SCHEDULED
    _record_event(
        db,
        row,
        event_type="scheduled",
        from_status=previous,
        to_status=AlprRequestStatus.SCHEDULED,
        message=payload.message or f"Scheduled for {payload.scheduled_date}.",
        actor_id=current_user.id,
    )
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.post("/requests/{request_id}/fulfill", response_model=AlprRequestOut)
def fulfill_request(
    request_id: UUID,
    payload: AlprRequestFulfill,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> AlprRequestOut:
    """Turn an approved request into real fleet movement.

    This is the only place a request touches the fleet, and it does so by
    calling the existing deployment and end-deployment services. No custody,
    status, or location rule is reimplemented here — if the ordinary workflow
    refuses a move, so does this.
    """
    from app.models import CustodyType, DeploymentStatus
    from app.ops import end_deployment, start_deployment
    from app.schemas import DeploymentEnd, DeploymentStart

    row = _load(db, request_id, current_user.organization_id)
    if row.status not in FULFILLABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve the request before fulfilling it.",
        )
    pending = [link for link in row.assets if link.fulfilled_at is None]
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select at least one trailer before fulfilling this request.",
        )

    # Caught here rather than letting apply_custody reject it mid-loop, which
    # would leave some trailers moved and some not.
    if payload.in_transit and not payload.carrier_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Shipping in transit requires a carrier name.",
        )

    if row.request_type == AlprRequestType.DEPLOY:
        agency_id = payload.agency_id or row.agency_id
        if agency_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Link the request to a customer/agency before deploying.",
            )
        agency = db.scalar(
            select(Agency)
            .where(Agency.id == agency_id)
            .where(Agency.organization_id == row.organization_id)
        )
        if agency is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agency not found")

        custody = CustodyType.IN_TRANSIT if payload.in_transit else CustodyType.CUSTOMER_AGENCY
        for link in pending:
            start = DeploymentStart(
                custody_type=custody,
                status=(
                    DeploymentStatus.IN_TRANSIT if payload.in_transit else DeploymentStatus.DEPLOYED
                ),
                agency_id=agency.id,
                carrier_name=payload.carrier_name,
                tracking_code=payload.tracking_code,
                # The request address is where the unit is actually going, which
                # can differ from the agency's registered address.
                address=row.address,
                notes=payload.notes or f"Fulfilling ALPR request {row.reference}.",
            )
            start_deployment(db, link.asset, start, current_user.id)
            link.fulfilled_at = _now()
    else:
        if payload.warehouse_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Choose the warehouse the trailers are returning to.",
            )
        warehouse = db.scalar(
            select(Warehouse)
            .where(Warehouse.id == payload.warehouse_id)
            .where(Warehouse.organization_id == row.organization_id)
        )
        if warehouse is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
        for link in pending:
            end = DeploymentEnd(
                warehouse_id=warehouse.id,
                notes=payload.notes or f"Pickup for ALPR request {row.reference}.",
            )
            end_deployment(db, link.asset, end, current_user.id)
            link.fulfilled_at = _now()

    previous = row.status
    row.status = AlprRequestStatus.IN_PROGRESS
    _record_event(
        db,
        row,
        event_type="fulfilled",
        from_status=previous,
        to_status=AlprRequestStatus.IN_PROGRESS,
        message=(
            f"Ran the {'deployment' if row.request_type == AlprRequestType.DEPLOY else 'return'} "
            f"workflow for {len(pending)} trailer{'' if len(pending) == 1 else 's'}."
        ),
        actor_id=current_user.id,
    )
    db.commit()
    return _serialize(db, _load(db, request_id, current_user.organization_id))
