from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import (
    AssetOperationalStatus,
    AssetType,
    CustodyType,
    DeploymentStatus,
    InspectionResult,
    InspectionStatus,
    IssueSource,
    RepairChannel,
    RootCauseCategory,
    UserRole,
    WorkOrderEventType,
    WorkOrderPriority,
    WorkOrderStatus,
)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    # No length rule here: sign-in must answer 401 for a wrong password rather
    # than a 422 that discloses the password policy.
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    organization_id: UUID
    # The agency a customer account is scoped to; null for internal roles.
    agency_id: UUID | None = None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1)
    role: UserRole = UserRole.READ_ONLY
    agency_id: UUID | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8)
    full_name: str | None = Field(None, min_length=1)
    role: UserRole | None = None
    is_active: bool | None = None
    agency_id: UUID | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class AssetAuthorizationGrant(BaseModel):
    asset_id: UUID
    can_view: bool = True
    can_report_issue: bool = False


def _normalize_plate(value: str | None) -> str | None:
    """Plates are compared by eye and by ALPR read, so store one casing."""
    cleaned = " ".join((value or "").split()).upper()
    return cleaned or None


def _normalize_plate_state(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    return cleaned or None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    vin: str
    license_plate: str | None = None
    license_plate_state: str | None = None
    make_model: str
    initial_purchase_cost: Decimal
    current_location: str
    current_custody_type: CustodyType | None = None
    carrier_name: str | None = None
    tracking_code: str | None = None
    asset_type: AssetType
    created_at: datetime
    current_status: DeploymentStatus | None = None
    operational_status: str | None = None  # Enum value (e.g., "deployed", "available")
    open_work_orders: int = 0
    total_repair_cost: Decimal = Decimal("0")
    lemon_flag: bool = False
    lemon_warning: str | None = None
    maintenance_cost_pct: Decimal | None = None
    downtime_days: Decimal = Decimal("0")
    repeat_failure_count: int = 0
    age_days: int | None = None
    latest_odometer_miles: Decimal | None = None
    replacement_score: Decimal | None = None
    is_archived: bool = False
    archived_at: datetime | None = None
    warehouse_id: UUID | None = None
    agency_id: UUID | None = None
    created_by_id: UUID | None = None
    updated_by_id: UUID | None = None
    updated_at: datetime | None = None


class AssetTypeKpis(BaseModel):
    asset_type: AssetType
    total_assets: int
    deployed: int
    in_maintenance: int
    idle_or_stored: int
    total_purchase_cost: Decimal
    downtime_hours_ytd: Decimal


class NonAlprInventoryRow(BaseModel):
    """Inventory-only rollup for asset types outside the ALPR lifecycle."""

    asset_type: AssetType
    total_assets: int
    in_service: int
    out_of_service: int
    retired: int
    total_purchase_cost: Decimal


class DashboardKpis(BaseModel):
    # Operational readiness is an ALPR Trailer concept; every count below is
    # scoped to this type. Other asset types roll up in non_alpr_inventory.
    asset_type_scope: str
    fleet_size: int
    deployed: int
    available: int
    in_transit: int = 0
    in_maintenance: int
    out_of_service: int = 0
    retired: int = 0
    total_purchase_cost: Decimal
    downtime_hours_ytd: Decimal
    by_asset_type: list[AssetTypeKpis]
    non_alpr_inventory: list[NonAlprInventoryRow] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    id: UUID
    event_type: str
    title: str
    status: str
    location: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    downtime_hours: Decimal | None = None
    notes: str | None = None


class AssetTimeline(BaseModel):
    asset: AssetOut
    events: list[TimelineEvent]
    total_downtime_hours: Decimal


class CustodyUpdate(BaseModel):
    custody_type: CustodyType
    location: str = Field(min_length=2, max_length=255)
    carrier_name: str | None = None
    tracking_code: str | None = Field(default=None, max_length=64)
    notes: str | None = None
    warehouse_id: UUID | None = None
    agency_id: UUID | None = None
    address: str | None = Field(default=None, max_length=255)
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class InspectionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    component: str
    sort_order: int
    result: InspectionResult
    notes: str | None = None
    generated_work_order_id: UUID | None = None


class InspectionPhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    content_type: str
    created_at: datetime


class InspectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    status: InspectionStatus
    started_at: datetime
    submitted_at: datetime | None = None
    notes: str | None = None
    source: str = "internal"
    items: list[InspectionItemOut] = []
    photos: list[InspectionPhotoOut] = []


class InspectionItemUpdate(BaseModel):
    result: InspectionResult
    notes: str | None = None


class MeterReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    odometer_miles: Decimal | None = None
    engine_hours: Decimal | None = None
    reading_date: datetime
    source: str
    notes: str | None = None
    created_at: datetime


class MeterReadingCreate(BaseModel):
    odometer_miles: Decimal | None = Field(default=None, ge=0)
    engine_hours: Decimal | None = Field(default=None, ge=0)
    reading_date: datetime | None = None
    notes: str | None = None


class PMStatusDetail(BaseModel):
    """Calculated PM status details."""
    status: str  # ok, due_soon, due, overdue
    next_due_date: datetime | None = None
    next_due_miles: Decimal | None = None
    next_due_hours: Decimal | None = None
    remaining_days: int | None = None
    remaining_miles: Decimal | None = None
    remaining_hours: Decimal | None = None


class MaintenanceScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    asset_name: str
    asset_type: str
    vin: str
    name: str
    description: str | None = None
    interval_miles: Decimal | None = None
    interval_engine_hours: Decimal | None = None
    interval_days: int | None = None
    interval_months: int | None = None
    due_soon_threshold_miles: Decimal
    due_soon_threshold_days: int
    last_completed_date: datetime | None = None
    last_completed_miles: Decimal | None = None
    last_completed_engine_hours: Decimal | None = None
    is_active: bool
    auto_create_work_order: bool
    pending_work_order_id: UUID | None = None
    current_odometer_miles: Decimal | None = None
    current_engine_hours: Decimal | None = None
    pm_status: str
    next_due_date: datetime | None = None
    next_due_miles: Decimal | None = None
    next_due_hours: Decimal | None = None
    remaining_days: int | None = None
    remaining_miles: Decimal | None = None
    remaining_hours: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class MaintenanceScheduleCreate(BaseModel):
    asset_id: UUID
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    interval_miles: Decimal | None = Field(default=None, ge=0)
    interval_engine_hours: Decimal | None = Field(default=None, ge=0)
    interval_days: int | None = Field(default=None, ge=1)
    interval_months: int | None = Field(default=None, ge=1)
    due_soon_threshold_miles: Decimal = Field(default=Decimal("500"), ge=0)
    due_soon_threshold_days: int = Field(default=7, ge=1)
    auto_create_work_order: bool = False


class MaintenanceScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    interval_miles: Decimal | None = Field(default=None, ge=0)
    interval_engine_hours: Decimal | None = Field(default=None, ge=0)
    interval_days: int | None = Field(default=None, ge=1)
    interval_months: int | None = Field(default=None, ge=1)
    due_soon_threshold_miles: Decimal | None = Field(default=None, ge=0)
    due_soon_threshold_days: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    auto_create_work_order: bool | None = None


class PMCompletionRecord(BaseModel):
    """Record a PM completion (can be linked to a work order or manual)."""
    completed_at: datetime | None = None
    odometer_miles: Decimal | None = Field(default=None, ge=0)
    engine_hours: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class InspectionSubmit(BaseModel):
    notes: str | None = None


class WorkOrderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: WorkOrderEventType
    previous_value: str | None = None
    new_value: str | None = None
    notes: str | None = None
    created_by: str | None = None
    created_by_id: UUID | None = None
    created_at: datetime


class WorkOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    asset: str
    asset_type: AssetType
    vin: str | None = None
    title: str
    description: str | None = None
    status: WorkOrderStatus
    opened_at: datetime
    closed_at: datetime | None = None
    downtime_hours: Decimal
    labor_cost: Decimal
    parts_cost: Decimal
    vendor_invoice_cost: Decimal = Decimal("0")
    total_repair_cost: Decimal = Decimal("0")
    inspection_id: UUID | None = None
    inspection_item_id: UUID | None = None
    failed_component: str | None = None
    repair_channel: RepairChannel = RepairChannel.INTERNAL
    vendor_name: str | None = None
    issue_source: IssueSource = IssueSource.MANUAL_REPORT
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    assigned_to_id: UUID | None = None
    assigned_to: str | None = None
    inspector: str | None = None
    created_by_id: UUID | None = None
    updated_by_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    investigation_notes: str | None = None
    repair_actions: str | None = None
    parts_used: str | None = None
    labor_hours: Decimal = Decimal("0")
    downtime_start: datetime | None = None
    downtime_end: datetime | None = None
    root_cause_category: RootCauseCategory | None = None
    root_cause_description: str | None = None
    corrective_action: str | None = None
    preventive_action: str | None = None
    completion_notes: str | None = None
    is_archived: bool = False
    location: str | None = None
    events: list[WorkOrderEventOut] = []


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    asset: str
    asset_type: AssetType
    location: str
    status: DeploymentStatus
    custody_type: CustodyType | None = None
    carrier_name: str | None = None
    tracking_code: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    notes: str | None = None
    end_reason: str | None = None
    completion_notes: str | None = None
    completed_by_id: UUID | None = None


class ChecklistOut(BaseModel):
    asset_type: AssetType
    components: list[str]


class FailureBar(BaseModel):
    component: str
    fail_count: int
    inspected_count: int
    failure_rate_pct: Decimal


class StockAlert(BaseModel):
    component: str
    prior_30d_failures: int
    last_30d_failures: int
    spike_pct: Decimal
    recommendation: str


class EfficiencyRow(BaseModel):
    channel: str
    work_order_count: int
    avg_turnaround_hours: Decimal
    avg_invoice_total: Decimal
    closed_jobs: int


class DeploymentStats(BaseModel):
    month: str
    deployment_count: int
    deployment_percentage: Decimal


class AnalyticsHub(BaseModel):
    asset_type: str
    failures: list[FailureBar]
    stock_alerts: list[StockAlert]
    efficiency: list[EfficiencyRow]
    root_causes: list[dict] = []
    repeat_failures: list[dict] = []
    deployment_stats: list[DeploymentStats] = []
    avg_repair_cost: Decimal = Decimal("0")
    avg_turnaround_hours: Decimal = Decimal("0")
    avg_downtime_hours: Decimal = Decimal("0")
    total_downtime_hours: Decimal = Decimal("0")
    total_uptime_hours: Decimal = Decimal("0")
    uptime_percentage: Decimal = Decimal("0")
    completed_work_orders: int = 0
    open_work_orders: int = 0
    total_deployments: int = 0
    active_deployments: int = 0
    avg_deployment_duration_days: Decimal = Decimal("0")
    maintenance_frequency_days: Decimal = Decimal("0")


class AssetCreate(BaseModel):
    vin: str = Field(min_length=4, max_length=32)
    license_plate: str | None = Field(default=None, max_length=16)
    license_plate_state: str | None = Field(default=None, max_length=2)
    make_model: str = Field(min_length=2, max_length=255)
    initial_purchase_cost: Decimal = Field(gt=0)
    current_location: str = Field(min_length=2, max_length=255)
    asset_type: AssetType
    current_custody_type: CustodyType = CustodyType.WAREHOUSE_DEPOT
    warehouse_id: UUID | None = None
    agency_id: UUID | None = None
    notes: str | None = None

    @field_validator("license_plate")
    @classmethod
    def clean_plate(cls, value: str | None) -> str | None:
        return _normalize_plate(value)

    @field_validator("license_plate_state")
    @classmethod
    def clean_plate_state(cls, value: str | None) -> str | None:
        return _normalize_plate_state(value)


class AssetImportRow(BaseModel):
    """Outcome for a single spreadsheet row."""

    row_number: int
    vin: str | None = None
    make_model: str | None = None
    errors: list[str] = Field(default_factory=list)
    created_asset_id: UUID | None = None


class AssetImportResult(BaseModel):
    filename: str
    committed: bool
    total_rows: int
    valid_rows: int
    error_rows: int
    created_count: int = 0
    rows: list[AssetImportRow] = Field(default_factory=list)


class AssetUpdate(BaseModel):
    vin: str | None = Field(default=None, min_length=4, max_length=32)
    license_plate: str | None = Field(default=None, max_length=16)
    license_plate_state: str | None = Field(default=None, max_length=2)
    make_model: str | None = Field(default=None, min_length=2, max_length=255)
    initial_purchase_cost: Decimal | None = Field(default=None, gt=0)
    asset_type: AssetType | None = None
    warehouse_id: UUID | None = None
    agency_id: UUID | None = None

    @field_validator("license_plate")
    @classmethod
    def clean_plate(cls, value: str | None) -> str | None:
        return _normalize_plate(value)

    @field_validator("license_plate_state")
    @classmethod
    def clean_plate_state(cls, value: str | None) -> str | None:
        return _normalize_plate_state(value)


class OperationalStatusUpdate(BaseModel):
    """Warehouse-side status correction that does not require an open deployment."""
    operational_status: AssetOperationalStatus
    warehouse_id: UUID | None = None
    notes: str | None = None


class DeploymentStart(BaseModel):
    """Start a deployment at a warehouse or agency.

    `location` is derived from whichever site is linked, so callers that pass a
    warehouse_id or agency_id can leave it out. It stays accepted on its own for
    field sites that are not in the directory yet.
    """

    location: str | None = Field(default=None, min_length=2, max_length=255)
    status: DeploymentStatus = DeploymentStatus.DEPLOYED
    custody_type: CustodyType = CustodyType.CUSTOMER_AGENCY
    carrier_name: str | None = None
    tracking_code: str | None = Field(default=None, max_length=64)
    notes: str | None = None
    warehouse_id: UUID | None = None
    agency_id: UUID | None = None
    address: str | None = Field(default=None, max_length=255)
    latitude: Decimal | None = None
    longitude: Decimal | None = None

    @model_validator(mode="after")
    def needs_somewhere_to_go(self):
        if not self.warehouse_id and not self.agency_id and not (self.location or "").strip():
            raise ValueError("Choose a warehouse or agency, or enter a location.")
        return self


class DeploymentEnd(BaseModel):
    """Legacy end deployment - replaced by EndDeploymentWorkflow."""
    location: str | None = Field(default=None, min_length=2, max_length=255)
    notes: str | None = None
    warehouse_id: UUID | None = None


class EndDeploymentWorkflow(BaseModel):
    """Complete end deployment workflow with next disposition."""
    disposition: str = Field(..., description="return_to_warehouse|transfer_to_agency|in_transit|maintenance|retired")
    completion_notes: str | None = None
    
    # Return to Warehouse
    destination_warehouse_id: UUID | None = None
    set_available: bool = False
    
    # Transfer to Another Agency
    next_agency_id: UUID | None = None
    create_next_deployment: bool = False
    next_deployment_location: str | None = None
    next_deployment_notes: str | None = None
    
    # In Transit
    transit_origin: str | None = None
    transit_destination: str | None = None
    carrier_name: str | None = None
    tracking_code: str | None = None
    departure_date: datetime | None = None
    expected_arrival_date: datetime | None = None
    
    # Maintenance
    maintenance_warehouse_id: UUID | None = None
    create_work_order: bool = False
    work_order_title: str | None = None
    work_order_description: str | None = None
    
    # Retired (out_of_service_reason kept for existing clients)
    out_of_service_reason: str | None = None


class DeploymentUpdate(BaseModel):
    notes: str | None = None
    location: str | None = Field(default=None, min_length=2, max_length=255)


class WorkOrderCreate(BaseModel):
    asset_id: UUID
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None
    status: WorkOrderStatus = WorkOrderStatus.OPEN
    labor_cost: Decimal = Field(default=Decimal("0"), ge=0)
    parts_cost: Decimal = Field(default=Decimal("0"), ge=0)
    vendor_invoice_cost: Decimal = Field(default=Decimal("0"), ge=0)
    downtime_hours: Decimal = Field(default=Decimal("0"), ge=0)
    repair_channel: RepairChannel = RepairChannel.INTERNAL
    vendor_name: str | None = None
    failed_component: str | None = None
    issue_source: IssueSource = IssueSource.MANUAL_REPORT
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    assigned_to_id: UUID | None = None


class WorkOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    status: WorkOrderStatus | None = None
    labor_cost: Decimal | None = Field(default=None, ge=0)
    parts_cost: Decimal | None = Field(default=None, ge=0)
    vendor_invoice_cost: Decimal | None = Field(default=None, ge=0)
    downtime_hours: Decimal | None = Field(default=None, ge=0)
    labor_hours: Decimal | None = Field(default=None, ge=0)
    repair_channel: RepairChannel | None = None
    vendor_name: str | None = None
    failed_component: str | None = None
    issue_source: IssueSource | None = None
    priority: WorkOrderPriority | None = None
    assigned_to_id: UUID | None = None
    investigation_notes: str | None = None
    repair_actions: str | None = None
    parts_used: str | None = None
    downtime_start: datetime | None = None
    downtime_end: datetime | None = None
    root_cause_category: RootCauseCategory | None = None
    root_cause_description: str | None = None
    corrective_action: str | None = None
    preventive_action: str | None = None
    completion_notes: str | None = None
    is_archived: bool | None = None


class RepairCostAdd(BaseModel):
    labor_cost: Decimal = Field(default=Decimal("0"), ge=0)
    parts_cost: Decimal = Field(default=Decimal("0"), ge=0)
    vendor_invoice_cost: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


class DirectoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    address: str | None = None
    agency_type: str | None = None
    contact_name: str | None = None
    specialty: str | None = None


class DirectoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    address: str | None = None
    agency_type: str | None = None
    contact_name: str | None = None
    specialty: str | None = None
    is_archived: bool | None = None


class DirectoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime | None = None
    created_by_id: UUID | None = None
    updated_by_id: UUID | None = None
    address: str | None = None
    agency_type: str | None = None
    contact_name: str | None = None
    specialty: str | None = None
    site_name: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class InspectionListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    asset: str
    asset_type: AssetType
    status: InspectionStatus
    started_at: datetime
    submitted_at: datetime | None = None
    notes: str | None = None
    fail_count: int = 0
    source: str = "internal"
    photo_count: int = 0
