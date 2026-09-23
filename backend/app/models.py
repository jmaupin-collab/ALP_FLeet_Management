from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _enum(enum_cls, name: str, length: int = 64) -> Enum:
    """VARCHAR-backed enums migrate cleanly from SQLite to PostgreSQL."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda members: [item.value for item in members],
    )


class AssetType(str, enum.Enum):
    ALPR_TRAILER = "ALPR Trailer"
    SEMI_TRUCK = "Semi Truck"
    FLEET_VEHICLE = "Fleet Vehicle"


class UserRole(str, enum.Enum):
    SYSTEM_ADMIN = "system_admin"  # Full system access across all organizations
    ORG_ADMIN = "org_admin"  # Admin within their organization
    FLEET_MANAGER = "fleet_manager"  # Program/Fleet Manager
    TECHNICIAN = "technician"  # Maintenance technician
    READ_ONLY = "read_only"  # Read-only access
    CUSTOMER = "customer"  # External customer/agency user (limited access)
    # Legacy roles for backward compatibility
    ADMIN = "admin"  # Maps to ORG_ADMIN
    DISPATCHER = "dispatcher"  # Maps to FLEET_MANAGER
    VIEWER = "viewer"  # Maps to READ_ONLY
    PROGRAM_MANAGER = "program_manager"  # Maps to FLEET_MANAGER


class DeploymentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    # Legacy statuses for backward compatibility
    STAGED = "staged"
    IN_TRANSIT = "in_transit"
    DEPLOYED = "deployed"
    IDLE = "idle"
    STORED = "stored"
    RETURNED = "returned"


class AssetOperationalStatus(str, enum.Enum):
    """Asset operational status - separate from deployment status."""
    AVAILABLE = "available"
    DEPLOYED = "deployed"
    IN_TRANSIT = "in_transit"
    MAINTENANCE = "maintenance"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED = "retired"


class CustodyType(str, enum.Enum):
    WAREHOUSE_DEPOT = "Warehouse Depot"
    IN_TRANSIT = "In Transit"
    CUSTOMER_AGENCY = "Customer / LE Agency"


class WorkOrderStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    IN_PROGRESS = "in_progress"
    WAITING_PARTS = "waiting_parts"
    WAITING_VENDOR = "waiting_vendor"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RepairChannel(str, enum.Enum):
    INTERNAL = "Internal"
    THIRD_PARTY = "Third-Party Vendor"


class IssueSource(str, enum.Enum):
    INSPECTION = "Inspection"
    MANUAL_REPORT = "Manual Report"
    PREVENTIVE_MAINTENANCE = "Preventive Maintenance"
    OTHER = "Other"


class WorkOrderPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RootCauseCategory(str, enum.Enum):
    MECHANICAL = "Mechanical Failure"
    ELECTRICAL = "Electrical Failure"
    WEAR = "Wear and Tear"
    INSTALLATION = "Installation Issue"
    MANUFACTURING = "Manufacturing Defect"
    OPERATOR = "Operator Damage"
    ENVIRONMENTAL = "Environmental Damage"
    SOFTWARE = "Software / Firmware"
    UNKNOWN = "Unknown"
    OTHER = "Other"


class WorkOrderEventType(str, enum.Enum):
    CREATED = "work_order_created"
    STATUS_CHANGED = "status_changed"
    TECHNICIAN_ASSIGNED = "technician_assigned"
    NOTES_ADDED = "notes_added"
    PARTS_ADDED = "parts_added"
    PARTS_REVERSED = "parts_reversed"
    VENDOR_ASSIGNED = "vendor_assigned"
    REPAIR_COST_ADDED = "repair_cost_added"
    ROOT_CAUSE_ENTERED = "root_cause_entered"
    COMPLETED = "work_order_completed"


class InspectionStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


class InspectionResult(str, enum.Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    NA = "n/a"


class Organization(Base):
    """Organization / Tenant entity for multi-tenancy."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    org_type: Mapped[str] = mapped_column(String(64), nullable=False)  # internal, customer, agency
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    settings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON settings
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(back_populates="organization")
    assets: Mapped[list[Asset]] = relationship(back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Customer accounts are scoped to exactly one agency and may only see that
    # agency's assets. Internal roles leave this null and are scoped by org.
    # SET NULL on delete keeps the failure closed: the customer sees nothing.
    agency_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.READ_ONLY, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_reset_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Tokens issued before this instant are rejected, so a password change ends
    # every outstanding session.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="users")

    created_deployments: Mapped[list[Deployment]] = relationship(
        back_populates="created_by",
        foreign_keys="Deployment.created_by_id",
    )
    created_work_orders: Mapped[list[MaintenanceWorkOrder]] = relationship(
        back_populates="created_by",
        foreign_keys="MaintenanceWorkOrder.created_by_id",
    )
    created_inspections: Mapped[list[Inspection]] = relationship(
        back_populates="created_by",
        foreign_keys="Inspection.created_by_id",
    )
    assigned_work_orders: Mapped[list[MaintenanceWorkOrder]] = relationship(
        back_populates="assigned_to",
        foreign_keys="MaintenanceWorkOrder.assigned_to_id",
    )


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(64), default="USA", nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    organization: Mapped[Organization | None] = relationship()


class Agency(Base):
    """Customers and law-enforcement agencies that take custody of assets."""

    __tablename__ = "agencies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    site_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agency_type: Mapped[str] = mapped_column(String(64), default="Law Enforcement", nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(64), default="USA", nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    organization: Mapped[Organization | None] = relationship()


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    specialty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    organization: Mapped[Organization | None] = relationship()


class AuditLog(Base):
    """Audit trail for important user actions."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON details
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class AssetAuthorization(Base):
    """Explicit asset access authorization for customer/agency users."""

    __tablename__ = "asset_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    can_view: Mapped[bool] = mapped_column(default=True, nullable=False)
    can_report_issue: Mapped[bool] = mapped_column(default=False, nullable=False)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GPSLocation(Base):
    """GPS/telematics location history for assets (provider-neutral)."""

    __tablename__ = "gps_locations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telematics_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)  # geotab, samsara, etc
    telematics_device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    heading: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)  # 0-360 degrees
    speed: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)  # mph or km/h
    accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)  # meters
    location_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="gps_locations")


class Asset(Base):
    """Current snapshot of an asset. Location/status history lives in child tables."""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vin: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    # Registration plate. Optional: trailers and yard equipment often have none,
    # and a plate can be reissued, so the VIN stays the unique identifier.
    license_plate: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    license_plate_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    make_model: Mapped[str] = mapped_column(String(255), nullable=False)
    initial_purchase_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    current_location: Mapped[str] = mapped_column(String(255), nullable=False)
    current_custody_type: Mapped[CustodyType | None] = mapped_column(_enum(CustodyType, "asset_custody_type"), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_type: Mapped[AssetType] = mapped_column(_enum(AssetType, "asset_type"), nullable=False, index=True)
    current_status: Mapped[DeploymentStatus | None] = mapped_column(_enum(DeploymentStatus, "current_status"), nullable=True, index=True)
    operational_status: Mapped[AssetOperationalStatus | None] = mapped_column(_enum(AssetOperationalStatus, "asset_operational_status"), nullable=True, index=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True)
    agency_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("agencies.id", ondelete="SET NULL"), nullable=True)
    # GPS/Telematics fields (provider-neutral)
    telematics_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telematics_device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="assets")
    gps_locations: Mapped[list[GPSLocation]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="GPSLocation.location_timestamp.desc()",
    )

    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="Deployment.started_at.desc()",
    )
    work_orders: Mapped[list[MaintenanceWorkOrder]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="MaintenanceWorkOrder.opened_at.desc()",
    )
    inspections: Mapped[list[Inspection]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="Inspection.started_at.desc()",
    )


class Deployment(Base):
    """Append-only location/status history. Close the open row, then insert a new one."""

    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    status: Mapped[DeploymentStatus] = mapped_column(_enum(DeploymentStatus, "deployment_status"), nullable=False, index=True)
    custody_type: Mapped[CustodyType | None] = mapped_column(_enum(CustodyType, "deployment_custody_type"), nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # End deployment workflow fields
    end_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)  # disposition type
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="deployments")
    created_by: Mapped[User | None] = relationship(back_populates="created_deployments", foreign_keys=[created_by_id])


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[InspectionStatus] = mapped_column(
        _enum(InspectionStatus, "inspection_status"), default=InspectionStatus.IN_PROGRESS, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="inspections")
    created_by: Mapped[User | None] = relationship(back_populates="created_inspections", foreign_keys=[created_by_id])
    items: Mapped[list[InspectionItem]] = relationship(
        back_populates="inspection",
        cascade="all, delete-orphan",
        order_by="InspectionItem.sort_order",
    )
    photos: Mapped[list[InspectionPhoto]] = relationship(
        back_populates="inspection",
        cascade="all, delete-orphan",
        order_by="InspectionPhoto.created_at",
    )


class InspectionItem(Base):
    __tablename__ = "inspection_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)
    result: Mapped[InspectionResult] = mapped_column(
        _enum(InspectionResult, "inspection_result"), default=InspectionResult.PENDING, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="SET NULL"), nullable=True
    )

    inspection: Mapped[Inspection] = relationship(back_populates="items")
    generated_work_order: Mapped[MaintenanceWorkOrder | None] = relationship(
        foreign_keys=[generated_work_order_id],
    )


class InspectionPhoto(Base):
    __tablename__ = "inspection_photos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    inspection: Mapped[Inspection] = relationship(back_populates="photos")


class MaintenanceWorkOrder(Base):
    """Append-only maintenance history used for downtime calculations."""

    __tablename__ = "maintenance_work_orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkOrderStatus] = mapped_column(_enum(WorkOrderStatus, "work_order_status"), nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downtime_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), nullable=False)
    labor_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    parts_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    inspection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inspections.id", ondelete="SET NULL", use_alter=True, name="fk_wo_inspection_id"),
        nullable=True,
    )
    failed_component: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repair_channel: Mapped[RepairChannel] = mapped_column(
        _enum(RepairChannel, "repair_channel"), default=RepairChannel.INTERNAL, nullable=False
    )
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inspection_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inspection_items.id", ondelete="SET NULL", use_alter=True, name="fk_wo_inspection_item_id"),
        nullable=True,
        unique=True,
    )
    issue_source: Mapped[IssueSource] = mapped_column(
        _enum(IssueSource, "issue_source"), default=IssueSource.MANUAL_REPORT, nullable=False
    )
    priority: Mapped[WorkOrderPriority] = mapped_column(
        _enum(WorkOrderPriority, "work_order_priority"), default=WorkOrderPriority.MEDIUM, nullable=False, index=True
    )
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    investigation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    parts_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_invoice_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    labor_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), nullable=False)
    downtime_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downtime_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    root_cause_category: Mapped[RootCauseCategory | None] = mapped_column(
        _enum(RootCauseCategory, "root_cause_category"), nullable=True
    )
    root_cause_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrective_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    preventive_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="work_orders")
    created_by: Mapped[User | None] = relationship(back_populates="created_work_orders", foreign_keys=[created_by_id])
    assigned_to: Mapped[User | None] = relationship(back_populates="assigned_work_orders", foreign_keys=[assigned_to_id])
    events: Mapped[list[WorkOrderEvent]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderEvent.created_at.asc()",
    )


class WorkOrderEvent(Base):
    """Append-only work-order activity. Current status lives on the parent row."""

    __tablename__ = "work_order_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[WorkOrderEventType] = mapped_column(
        _enum(WorkOrderEventType, "work_order_event_type", length=64), nullable=False, index=True
    )
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    work_order: Mapped[MaintenanceWorkOrder] = relationship(back_populates="events")
    created_by: Mapped[User | None] = relationship()


class IntervalType(str, enum.Enum):
    """Preventive maintenance interval types."""
    MILEAGE = "mileage"
    ENGINE_HOURS = "engine_hours"
    CALENDAR_DAYS = "calendar_days"
    CALENDAR_MONTHS = "calendar_months"


class PMStatus(str, enum.Enum):
    """Preventive maintenance status."""
    OK = "ok"
    DUE_SOON = "due_soon"
    DUE = "due"
    OVERDUE = "overdue"


class LocationSource(str, enum.Enum):
    """Source of asset location data for map display."""
    GPS = "gps"  # Live GPS from telematics
    DEPLOYMENT = "deployment"  # Active deployment location
    CUSTOMER = "customer"  # Customer/agency assignment
    WAREHOUSE = "warehouse"  # Warehouse/depot assignment
    IN_TRANSIT = "in_transit"  # Currently in transit
    MANUAL = "manual"  # Manually entered
    UNKNOWN = "unknown"  # No location available


class MeterReading(Base):
    """Historical odometer and engine hour readings for telematics integration."""

    __tablename__ = "meter_readings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    odometer_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    engine_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reading_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)  # manual, geotab, samsara, etc
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="meter_readings")


class MaintenanceScheduleTemplate(Base):
    """PM schedule templates by asset type or make/model."""

    __tablename__ = "maintenance_schedule_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_type: Mapped[AssetType | None] = mapped_column(_enum(AssetType, "template_asset_type"), nullable=True)
    make_model_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interval_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    interval_engine_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    interval_days: Mapped[int | None] = mapped_column(nullable=True)
    interval_months: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MaintenanceSchedule(Base):
    """Preventive maintenance schedules for individual assets."""

    __tablename__ = "maintenance_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    interval_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    interval_engine_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    interval_days: Mapped[int | None] = mapped_column(nullable=True)
    interval_months: Mapped[int | None] = mapped_column(nullable=True)
    due_soon_threshold_miles: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("500"), nullable=False)
    due_soon_threshold_days: Mapped[int] = mapped_column(default=7, nullable=False)
    last_completed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_completed_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    last_completed_engine_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    auto_create_work_order: Mapped[bool] = mapped_column(default=False, nullable=False)
    pending_work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="pm_schedules")
    completions: Mapped[list[PMCompletion]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="PMCompletion.completed_at.desc()",
    )


class PMCompletion(Base):
    """Historical record of preventive maintenance completions."""

    __tablename__ = "pm_completions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odometer_miles: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    engine_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    schedule: Mapped[MaintenanceSchedule] = relationship(back_populates="completions")


# Add relationships to Asset
Asset.meter_readings = relationship(
    "MeterReading",
    back_populates="asset",
    cascade="all, delete-orphan",
    order_by="MeterReading.reading_date.desc()",
)
Asset.pm_schedules = relationship(
    "MaintenanceSchedule",
    back_populates="asset",
    cascade="all, delete-orphan",
    order_by="MaintenanceSchedule.name",
)


class DocumentType(str, enum.Enum):
    REGISTRATION = "registration"
    INSURANCE = "insurance"
    TITLE = "title"
    PURCHASE_INVOICE = "purchase_invoice"
    WARRANTY = "warranty"
    PERMIT = "permit"
    SERVICE_RECORD = "service_record"
    OTHER = "other"


class InventoryTxnType(str, enum.Enum):
    RECEIPT = "receipt"
    WORK_ORDER_USAGE = "work_order_usage"
    ADJUSTMENT = "adjustment"
    RETURN = "return"
    TRANSFER = "transfer"
    REVERSAL = "reversal"


class AttentionSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AttentionStatus(str, enum.Enum):
    OPEN = "open"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class AssetDocument(Base):
    """Private asset documents. Metadata and files are management-only."""

    __tablename__ = "asset_documents"
    __table_args__ = (
        Index("ix_asset_documents_org_asset", "organization_id", "asset_id"),
        Index("ix_asset_documents_expiration", "expiration_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[DocumentType] = mapped_column(_enum(DocumentType, "document_type"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    # How far ahead of expiration_date to start reminding. A registration renewal
    # wants more lead time than a warranty, so it is per document rather than a
    # single global constant.
    reminder_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    asset: Mapped[Asset] = relationship(back_populates="documents")
    uploaded_by: Mapped[User | None] = relationship()


class InventoryPart(Base):
    __tablename__ = "inventory_parts"
    __table_args__ = (UniqueConstraint("organization_id", "sku", name="uq_inventory_parts_org_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warehouse_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bin_location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reorder_point: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reorder_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    compatible_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    transactions: Mapped[list[InventoryTransaction]] = relationship(
        back_populates="part",
        cascade="all, delete-orphan",
        order_by="InventoryTransaction.created_at.desc()",
    )
    work_order_lines: Mapped[list[WorkOrderPart]] = relationship(back_populates="part")


class InventoryTransaction(Base):
    """Append-only quantity ledger. Never overwrite quantity_on_hand without a row here."""

    __tablename__ = "inventory_transactions"
    __table_args__ = (Index("ix_inventory_txn_org_part", "organization_id", "part_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_parts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    txn_type: Mapped[InventoryTxnType] = mapped_column(_enum(InventoryTxnType, "inventory_txn_type"), nullable=False, index=True)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    allow_negative: Mapped[bool] = mapped_column(default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    part: Mapped[InventoryPart] = relationship(back_populates="transactions")
    work_order: Mapped[MaintenanceWorkOrder | None] = relationship()
    asset: Mapped[Asset | None] = relationship()
    created_by: Mapped[User | None] = relationship()


class WorkOrderPart(Base):
    __tablename__ = "work_order_parts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_parts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    work_order: Mapped[MaintenanceWorkOrder] = relationship(back_populates="part_lines")
    part: Mapped[InventoryPart] = relationship(back_populates="work_order_lines")


class AttentionItem(Base):
    """In-app attention items. condition_key prevents duplicate open rows."""

    __tablename__ = "attention_items"
    __table_args__ = (
        UniqueConstraint("organization_id", "condition_key", name="uq_attention_org_condition"),
        Index("ix_attention_org_status", "organization_id", "status", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    condition_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[AttentionSeverity] = mapped_column(
        _enum(AttentionSeverity, "attention_severity"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("maintenance_work_orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("inventory_parts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("asset_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    link_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[AttentionStatus] = mapped_column(
        _enum(AttentionStatus, "attention_status"), default=AttentionStatus.OPEN, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


Asset.documents = relationship(
    "AssetDocument",
    back_populates="asset",
    cascade="all, delete-orphan",
    order_by="AssetDocument.uploaded_at.desc()",
)
MaintenanceWorkOrder.part_lines = relationship(
    "WorkOrderPart",
    back_populates="work_order",
    cascade="all, delete-orphan",
    order_by="WorkOrderPart.created_at.asc()",
)
