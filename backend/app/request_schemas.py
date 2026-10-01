"""Payload shapes for the public ALPR request form and the internal Request Center.

Kept apart from `app.schemas` because the public half is the one untrusted
surface in the API and benefits from being read in one piece. Every public
field is length-capped and whitespace-normalised, and nothing here can name an
asset, a VIN, or any other internal identifier.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import AlprRequestStatus, AlprRequestType

# A request for more trailers than this is a conversation, not a form entry.
MAX_TRAILERS_PER_REQUEST = 50
MAX_NOTES = 2000


def _strip_controls(value: str, keep_newlines: bool = False) -> str:
    """Replace control characters with a space rather than deleting them.

    Deleting would silently weld words together ("Houston\\tPD" -> "HoustonPD").
    They are removed at all because they have no business in a name or address
    and are the usual carrier for log-injection and CSV-formula tricks.
    """
    kept = "\n" if keep_newlines else ""
    return "".join(char if char in kept or char >= " " else " " for char in value)


def _collapse(value: str | None) -> str | None:
    """Normalise whitespace and treat a blank entry as absent."""
    if value is None:
        return None
    cleaned = " ".join(_strip_controls(value).split())
    return cleaned or None


def _collapse_multiline(value: str | None) -> str | None:
    """Same cleanup for notes, but paragraph breaks survive."""
    if value is None:
        return None
    lines = [" ".join(line.split()) for line in _strip_controls(value, keep_newlines=True).splitlines()]
    cleaned = "\n".join(lines).strip()
    return cleaned or None


class PublicAlprRequestCreate(BaseModel):
    """What an anonymous visitor may submit.

    Note what is absent: no asset id, no VIN, no organization, no status. A
    submitter cannot aim a request at a specific trailer or pre-approve it.
    """

    model_config = ConfigDict(extra="forbid")

    request_type: AlprRequestType
    agency_name: str = Field(min_length=2, max_length=255)
    requester_name: str = Field(min_length=2, max_length=255)
    requester_email: EmailStr
    requester_phone: str | None = Field(default=None, max_length=64)
    requested_date: date | None = None
    address: str = Field(min_length=5, max_length=512)
    quantity: int = Field(default=1, ge=1, le=MAX_TRAILERS_PER_REQUEST)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    # Turnstile / reCAPTCHA token. Optional in the shape so a deployment with
    # verification switched off does not have to send a placeholder.
    captcha_token: str | None = Field(default=None, max_length=4096)

    @field_validator("agency_name", "requester_name", "address")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        cleaned = _collapse(value)
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("requester_phone")
    @classmethod
    def clean_phone(cls, value: str | None) -> str | None:
        return _collapse(value)

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        return _collapse_multiline(value)


class PublicAlprRequestAccepted(BaseModel):
    """The only thing a submitter gets back.

    A reference and a status, deliberately nothing about the fleet: no
    availability, no asset, no agency record, not even the internal id.
    """

    reference: str
    status: AlprRequestStatus
    message: str


class PublicFormConfig(BaseModel):
    """What the public form needs in order to render.

    The CAPTCHA site key is public by design; the secret stays server-side.
    """

    captcha_provider: str
    captcha_site_key: str | None = None
    max_quantity: int = MAX_TRAILERS_PER_REQUEST


class AlprRequestEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    from_status: AlprRequestStatus | None = None
    to_status: AlprRequestStatus | None = None
    message: str | None = None
    actor_id: UUID | None = None
    actor_name: str | None = None
    created_at: datetime


class AlprRequestAssetOut(BaseModel):
    id: UUID
    asset_id: UUID
    vin: str
    make_model: str
    license_plate: str | None = None
    operational_status: str | None = None
    fulfilled_at: datetime | None = None


class AlprRequestOut(BaseModel):
    """Internal view. Staff-only, so it may carry the operational detail."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference: str
    organization_id: UUID
    request_type: AlprRequestType
    status: AlprRequestStatus
    agency_name: str
    agency_id: UUID | None = None
    requester_name: str
    requester_email: str
    requester_phone: str | None = None
    requested_date: date | None = None
    address: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    quantity: int
    notes: str | None = None
    review_notes: str | None = None
    scheduled_date: date | None = None
    reviewed_by_id: UUID | None = None
    reviewed_at: datetime | None = None
    completed_at: datetime | None = None
    completed_by_id: UUID | None = None
    completion_notes: str | None = None
    source_ip: str | None = None
    captcha_verified: bool = False
    created_at: datetime
    updated_at: datetime | None = None
    assets: list[AlprRequestAssetOut] = Field(default_factory=list)
    events: list[AlprRequestEventOut] = Field(default_factory=list)


class AlprRequestUpdate(BaseModel):
    """Operational corrections staff may make to an intake record.

    Status is not here: it moves only through the status endpoint, so every
    transition is validated and written to the event trail.
    """

    agency_name: str | None = Field(default=None, min_length=2, max_length=255)
    agency_id: UUID | None = None
    requester_name: str | None = Field(default=None, min_length=2, max_length=255)
    requester_email: EmailStr | None = None
    requester_phone: str | None = Field(default=None, max_length=64)
    requested_date: date | None = None
    address: str | None = Field(default=None, min_length=5, max_length=512)
    quantity: int | None = Field(default=None, ge=1, le=MAX_TRAILERS_PER_REQUEST)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    review_notes: str | None = Field(default=None, max_length=MAX_NOTES)


class AlprRequestStatusChange(BaseModel):
    status: AlprRequestStatus
    message: str | None = Field(default=None, max_length=MAX_NOTES)


class AlprRequestAssetsSet(BaseModel):
    """Which trailers staff picked. Replaces the current selection wholesale."""

    asset_ids: list[UUID] = Field(default_factory=list)


class AlprRequestSchedule(BaseModel):
    scheduled_date: date
    message: str | None = Field(default=None, max_length=MAX_NOTES)


class AlprRequestFulfill(BaseModel):
    """Run the real lifecycle workflow for this request.

    Carries only the parameters the existing deployment/return workflow needs;
    the request supplies the rest. No new lifecycle rules live here.
    """

    # Deploy: where the trailers are going. Defaults to the request's linked
    # agency when it has one.
    agency_id: UUID | None = None
    # Pickup: where they are coming back to.
    warehouse_id: UUID | None = None
    carrier_name: str | None = Field(default=None, max_length=255)
    tracking_code: str | None = Field(default=None, max_length=64)
    # Ship instead of hand over directly. Requires a carrier name.
    in_transit: bool = False
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
