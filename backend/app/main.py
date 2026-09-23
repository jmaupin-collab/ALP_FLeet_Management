from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.analytics import build_analytics
from app.auth import create_access_token, hash_password, verify_password
from app.bulk_import import (
    COLUMNS as IMPORT_COLUMNS,
    ImportFileError,
    MAX_IMPORT_ROWS,
    parse_sheet,
    prepare_rows,
    template_csv,
    template_xlsx,
)
from app.checklists import checklist_for
from app.config import get_settings
from app.database import SessionLocal, ensure_schema, get_db
from app.deps import (
    get_current_user,
    require_admin,
    require_fleet_admin,
    require_operator,
    require_permission,
    require_reporter,
)
from app.exports import analytics_csv
from app.models import (
    Agency,
    Asset,
    AssetAuthorization,
    AssetType,
    Deployment,
    InspectionPhoto,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    MeterReading,
    PMCompletion,
    PMStatus,
    RepairChannel,
    User,
    UserRole,
    Vendor,
    Warehouse,
    WorkOrderPriority,
    WorkOrderStatus,
)
from app.rbac import (
    can_access_organization,
    canonical_role,
    filter_assets_by_access,
    is_customer,
    filter_by_authorized_assets,
    is_system_admin,
    require_assignable_role,
    require_asset_access,
    require_deployment_access,
    require_inspection_access,
    require_pm_schedule_access,
    require_work_order_access,
)
from app.pm import (
    get_latest_meter_reading,
    list_pm_schedules_with_status,
    record_meter_reading,
    serialize_meter_reading,
    serialize_pm_schedule,
)
from app.schemas import (
    AnalyticsHub,
    AssetAuthorizationGrant,
    AssetCreate,
    AssetImportResult,
    AssetImportRow,
    AssetOut,
    AssetTimeline,
    AssetUpdate,
    ChecklistOut,
    CustodyUpdate,
    DashboardKpis,
    DeploymentEnd,
    DeploymentOut,
    DeploymentStart,
    DeploymentUpdate,
    DirectoryCreate,
    DirectoryOut,
    DirectoryUpdate,
    EndDeploymentWorkflow,
    InspectionItemUpdate,
    InspectionListOut,
    InspectionOut,
    InspectionSubmit,
    LoginRequest,
    MaintenanceScheduleCreate,
    MaintenanceScheduleOut,
    MaintenanceScheduleUpdate,
    MeterReadingCreate,
    MeterReadingOut,
    OperationalStatusUpdate,
    PasswordChange,
    PMCompletionRecord,
    RepairCostAdd,
    Token,
    UserCreate,
    UserOut,
    UserUpdate,
    WorkOrderCreate,
    WorkOrderOut,
    WorkOrderUpdate,
)
from app.ops import (
    add_repair_cost,
    archive_asset,
    cancel_in_progress_inspection,
    close_work_order,
    create_agency,
    create_asset,
    create_vendor,
    create_warehouse,
    create_work_order,
    delete_asset,
    delete_or_archive_agency,
    delete_or_archive_vendor,
    delete_or_archive_warehouse,
    delete_work_order,
    end_deployment,
    end_deployment_workflow,
    list_agencies,
    list_inspections,
    list_vendors,
    list_warehouses,
    load_work_order,
    patch_directory,
    serialize_directory,
    set_operational_status,
    start_deployment,
    update_asset_details,
    update_deployment_notes,
    update_work_order,
)
from app.attention import router as attention_router
from app.database import (
    backfill_customer_agency_ids,
    count_unscoped_customers,
    migrate_directory_org_ids,
    normalize_legacy_role_values,
)
from app.documents import router as documents_router
from app.inspection_photos import photo_response
from app.inventory import router as inventory_router
from app.location import get_asset_location
from app.seed_quick import seed_quick
from app.utilization import router as utilization_router
from app.services import (
    apply_custody,
    asset_query,
    asset_timeline,
    dashboard_kpis,
    effective_operational_status,
    get_asset,
    is_retired,
    list_deployments,
    list_work_orders,
    load_inspection,
    serialize_asset,
    serialize_inspection,
    serialize_work_order,
    serialize_deployment,
    start_inspection,
    submit_inspection,
    update_inspection_item,
    create_customer_inspection,
)
from app.workorders import list_users as get_assignable_users, serialize_work_order as serialize_wo_detail, work_order_query

settings = get_settings()
logger = logging.getLogger("fleet")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    migrate_directory_org_ids()
    normalized = normalize_legacy_role_values()
    if normalized:
        logger.info("Normalized %d user rows from legacy role values.", normalized)
    scoped = backfill_customer_agency_ids()
    if scoped:
        logger.info("Scoped %d customer account(s) to their agency.", scoped)
    unscoped = count_unscoped_customers()
    if unscoped:
        logger.warning(
            "%d customer account(s) have no agency assigned and will see no assets. "
            "Set an agency on each from the Admin console.",
            unscoped,
        )
    if settings.should_seed_demo_data():
        logger.warning("SEED_DEMO_DATA is enabled — creating demo accounts with well-known passwords.")
        db = SessionLocal()
        try:
            seed_quick(db)
        finally:
            db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(documents_router)
app.include_router(utilization_router)
app.include_router(inventory_router)
app.include_router(attention_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def log_unhandled_exception(request, exc):
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return Token(access_token=create_access_token(user.id, user.email))


@app.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@app.get("/dashboard/kpis", response_model=DashboardKpis)
def get_dashboard_kpis(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DashboardKpis:
    return dashboard_kpis(db, current_user.organization_id, current_user)


@app.get("/assets", response_model=list[AssetOut])
def list_assets(
    q: str | None = Query(default=None, description="Search VIN, make/model, or location"),
    asset_type: AssetType | None = Query(default=None),
    location: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssetOut]:
    # Apply organization filtering based on user's role
    query = asset_query(db, q=q, asset_type=asset_type, location=location, include_archived=include_archived, organization_id=current_user.organization_id)
    query = filter_assets_by_access(query, current_user, db)
    rows = db.scalars(query).all()
    return [serialize_asset(row) for row in rows]


def _org_directory_names(db: Session, model, organization_id: UUID) -> list[str]:
    return list(
        db.scalars(
            select(model.name)
            .where(model.organization_id == organization_id, model.is_archived.is_(False))
            .order_by(model.name)
        ).all()
    )


@app.get("/assets/import/template")
def get_asset_import_template(
    file_format: str = Query(default="xlsx", alias="format", pattern="^(xlsx|csv)$"),
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Response:
    """Download an import sheet carrying the expected headers and this org's site names."""
    warehouses = _org_directory_names(db, Warehouse, current_user.organization_id)
    agencies = _org_directory_names(db, Agency, current_user.organization_id)

    if file_format == "csv":
        content = template_csv(warehouses, agencies)
        media_type = "text/csv; charset=utf-8"
        filename = "asset-import-template.csv"
    else:
        content = template_xlsx(warehouses, agencies)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "asset-import-template.xlsx"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/assets/import/columns")
def get_asset_import_columns(
    current_user: User = Depends(require_operator),
) -> dict:
    """Column reference so the upload screen can explain the format without hardcoding it."""
    return {
        "max_rows": MAX_IMPORT_ROWS,
        "columns": [
            {"label": column.label, "required": column.required, "help": column.help}
            for column in IMPORT_COLUMNS
        ],
    }


@app.post("/assets/import", response_model=AssetImportResult)
async def import_assets(
    file: UploadFile = File(...),
    commit: bool = Query(default=False, description="Validate only; set true to write the rows."),
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetImportResult:
    """Validate a spreadsheet of assets, and write it only when every row is clean.

    The default dry run lets the caller show row-level errors before anything is
    created. A commit runs in one transaction, so the upload is all or nothing.
    """
    data = await file.read()
    try:
        parsed = parse_sheet(file.filename or "", data)
        prepared = prepare_rows(db, parsed, current_user.organization_id)
    except ImportFileError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    rows = [
        AssetImportRow(
            row_number=row.row_number,
            vin=row.vin,
            make_model=row.make_model,
            errors=row.errors,
        )
        for row in prepared
    ]
    error_rows = sum(1 for row in prepared if row.errors)
    result = AssetImportResult(
        filename=file.filename or "upload",
        committed=False,
        total_rows=len(prepared),
        valid_rows=len(prepared) - error_rows,
        error_rows=error_rows,
        rows=rows,
    )

    # A dry run, or a commit the file has not earned yet, both stop here with the
    # per-row detail intact so the caller can show what needs fixing.
    if not commit or error_rows:
        return result

    by_row = {row.row_number: row for row in rows}
    try:
        for row in prepared:
            asset = create_asset(db, row.payload, current_user.id, commit=False)
            by_row[row.row_number].created_asset_id = asset.id
        db.commit()
    except Exception:
        db.rollback()
        raise

    result.committed = True
    result.created_count = len(prepared)
    return result


@app.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset_summary(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(asset)


@app.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def post_asset(
    payload: AssetCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    return serialize_asset(create_asset(db, payload, current_user.id))


@app.patch("/assets/{asset_id}", response_model=AssetOut)
def patch_asset(
    asset_id: UUID,
    payload: AssetUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(update_asset_details(db, asset, payload, current_user.id))


@app.post("/assets/{asset_id}/archive", response_model=AssetOut)
def post_archive_asset(
    asset_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(archive_asset(db, asset, current_user.id, archived=True))


@app.post("/assets/{asset_id}/restore", response_model=AssetOut)
def post_restore_asset(
    asset_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(archive_asset(db, asset, current_user.id, archived=False))


@app.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_asset(
    asset_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> None:
    """Delete asset. Org/System admins can force-delete assets with operational history (organization-scoped)."""
    asset = require_asset_access(db, current_user, asset_id)
    delete_asset(db, asset, force=True)


@app.post("/assets/{asset_id}/deployments", response_model=AssetOut)
def post_start_deployment(
    asset_id: UUID,
    payload: DeploymentStart,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(start_deployment(db, asset, payload, current_user.id))


@app.post("/assets/{asset_id}/deployments/end", response_model=AssetOut)
def post_end_deployment(
    asset_id: UUID,
    payload: DeploymentEnd,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(end_deployment(db, asset, payload, current_user.id))


@app.post("/assets/{asset_id}/deployments/end-workflow", response_model=AssetOut)
def post_end_deployment_workflow(
    asset_id: UUID,
    payload: EndDeploymentWorkflow,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    """End deployment with complete workflow handling all disposition options."""
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(end_deployment_workflow(db, asset, payload, current_user.id))


@app.get("/assets/{asset_id}/timeline", response_model=AssetTimeline)
def get_asset_timeline(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssetTimeline:
    asset = require_asset_access(db, current_user, asset_id)
    timeline = asset_timeline(db, asset_id)
    if timeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return timeline


@app.get("/assets/{asset_id}/checklist", response_model=ChecklistOut)
def get_asset_checklist(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChecklistOut:
    asset = require_asset_access(db, current_user, asset_id)
    return ChecklistOut(asset_type=asset.asset_type, components=checklist_for(asset.asset_type))


@app.post("/assets/{asset_id}/inspections", response_model=InspectionOut)
def create_inspection(
    asset_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> InspectionOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_inspection(start_inspection(db, asset, current_user.id, asset.organization_id))


@app.post("/assets/{asset_id}/customer-inspections", response_model=InspectionOut, status_code=status.HTTP_201_CREATED)
async def submit_customer_inspection(
    asset_id: UUID,
    notes: str | None = Form(default=None),
    photos: list[UploadFile] = File(default=[]),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InspectionOut:
    if not is_customer(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only customers can submit photo inspections")
    asset = require_asset_access(db, current_user, asset_id)
    uploads = [photo for photo in photos if photo.filename]
    return serialize_inspection(
        create_customer_inspection(db, asset, current_user.id, current_user.organization_id, notes, uploads)
    )


@app.post("/assets/{asset_id}/operational-status", response_model=AssetOut)
def post_operational_status(
    asset_id: UUID,
    payload: OperationalStatusUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    return serialize_asset(set_operational_status(db, asset, payload, current_user.id))


@app.post("/assets/{asset_id}/custody", response_model=AssetOut)
def update_asset_custody(
    asset_id: UUID,
    payload: CustodyUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AssetOut:
    asset = require_asset_access(db, current_user, asset_id)
    apply_custody(db, asset, payload, current_user.id)
    return serialize_asset(get_asset(db, asset_id))


@app.patch("/inspections/{inspection_id}/items/{item_id}", response_model=InspectionOut)
def patch_inspection_item(
    inspection_id: UUID,
    item_id: UUID,
    payload: InspectionItemUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> InspectionOut:
    inspection = require_inspection_access(db, current_user, inspection_id)
    update_inspection_item(db, inspection, item_id, payload.result, payload.notes, current_user.id)
    return serialize_inspection(load_inspection(db, inspection_id))


@app.post("/inspections/{inspection_id}/submit", response_model=InspectionOut)
def complete_inspection(
    inspection_id: UUID,
    payload: InspectionSubmit,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> InspectionOut:
    inspection = require_inspection_access(db, current_user, inspection_id)
    return serialize_inspection(submit_inspection(db, inspection, payload.notes))


@app.get("/inspections", response_model=list[InspectionListOut])
def get_inspections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InspectionListOut]:
    return list_inspections(db, current_user)


@app.get("/inspections/{inspection_id}", response_model=InspectionOut)
def get_inspection(
    inspection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InspectionOut:
    inspection = require_inspection_access(db, current_user, inspection_id)
    loaded = load_inspection(db, inspection.id)
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found")
    return serialize_inspection(loaded)


@app.get("/inspections/{inspection_id}/photos/{photo_id}")
def get_inspection_photo(
    inspection_id: UUID,
    photo_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_inspection_access(db, current_user, inspection_id)
    photo = db.get(InspectionPhoto, photo_id)
    if photo is None or photo.inspection_id != inspection_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return photo_response(photo)


@app.post("/inspections/{inspection_id}/cancel", response_model=InspectionOut)
def post_cancel_inspection(
    inspection_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> InspectionOut:
    inspection = require_inspection_access(db, current_user, inspection_id)
    return serialize_inspection(cancel_in_progress_inspection(db, inspection, current_user.id))


@app.get("/work-orders/users")
def get_work_order_users(
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Get list of users who can be assigned to work orders."""
    return get_assignable_users(db, current_user)


@app.get("/work-orders", response_model=list[WorkOrderOut])
def get_work_orders(
    status: WorkOrderStatus | None = Query(default=None),
    asset_type: AssetType | None = Query(default=None),
    asset_id: UUID | None = Query(default=None),
    priority: WorkOrderPriority | None = Query(default=None),
    component: str | None = Query(default=None),
    assigned_to_id: UUID | None = Query(default=None),
    location: str | None = Query(default=None),
    vendor: str | None = Query(default=None),
    repair_channel: RepairChannel | None = Query(default=None),
    opened_from: datetime | None = Query(default=None),
    opened_to: datetime | None = Query(default=None),
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkOrderOut]:
    stmt = work_order_query(
        db,
        status=status,
        asset_type=asset_type,
        asset_id=asset_id,
        priority=priority,
        component=component,
        assigned_to_id=assigned_to_id,
        location=location,
        vendor=vendor,
        repair_channel=repair_channel,
        opened_from=opened_from,
        opened_to=opened_to,
        organization_id=current_user.organization_id,
        include_archived=include_archived,
    )
    stmt = filter_by_authorized_assets(stmt, current_user, db, MaintenanceWorkOrder.asset_id)
    rows = db.scalars(stmt).all()
    return [serialize_work_order(row) for row in rows]


@app.post("/work-orders", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def post_work_order(
    payload: WorkOrderCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> WorkOrderOut:
    require_asset_access(db, current_user, payload.asset_id)
    return serialize_wo_detail(create_work_order(db, payload, current_user.id), include_events=True)


@app.get("/work-orders/{wo_id}", response_model=WorkOrderOut)
def get_work_order(
    wo_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkOrderOut:
    wo = require_work_order_access(db, current_user, wo_id)
    loaded = load_work_order(db, wo.id)
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work order not found")
    return serialize_wo_detail(loaded, include_events=True)


@app.patch("/work-orders/{wo_id}", response_model=WorkOrderOut)
def patch_work_order(
    wo_id: UUID,
    payload: WorkOrderUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> WorkOrderOut:
    wo = require_work_order_access(db, current_user, wo_id)
    return serialize_wo_detail(update_work_order(db, wo, payload, current_user.id), include_events=True)


@app.post("/work-orders/{wo_id}/close", response_model=WorkOrderOut)
def post_close_work_order(
    wo_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> WorkOrderOut:
    wo = require_work_order_access(db, current_user, wo_id)
    return serialize_wo_detail(close_work_order(db, wo, current_user.id), include_events=True)


@app.post("/work-orders/{wo_id}/costs", response_model=WorkOrderOut)
def post_add_cost(
    wo_id: UUID,
    payload: RepairCostAdd,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> WorkOrderOut:
    wo = require_work_order_access(db, current_user, wo_id)
    return serialize_wo_detail(add_repair_cost(db, wo, payload, current_user.id), include_events=True)


@app.delete("/work-orders/{wo_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_work_order(
    wo_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> None:
    """Delete work order. Org/System admins can delete work orders (organization-scoped)."""
    wo = require_work_order_access(db, current_user, wo_id)
    delete_work_order(db, wo)


@app.get("/deployments", response_model=list[DeploymentOut])
def get_deployments(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DeploymentOut]:
    return list_deployments(db, current_user.organization_id, current_user)


@app.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
def patch_deployment(
    deployment_id: UUID,
    payload: DeploymentUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DeploymentOut:
    deployment = require_deployment_access(db, current_user, deployment_id)
    row = update_deployment_notes(db, deployment_id, payload.notes, payload.location, current_user.id)
    return serialize_deployment(row)


@app.get("/warehouses", response_model=list[DirectoryOut])
def get_warehouses(
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DirectoryOut]:
    # System Admin lists only their assigned organization (no org-switcher).
    return [serialize_directory(row) for row in list_warehouses(db, current_user.organization_id, include_archived)]


@app.post("/warehouses", response_model=DirectoryOut, status_code=status.HTTP_201_CREATED)
def post_warehouse(
    payload: DirectoryCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    return serialize_directory(create_warehouse(db, payload, current_user.organization_id, current_user.id))


@app.patch("/warehouses/{row_id}", response_model=DirectoryOut)
def patch_warehouse(
    row_id: UUID,
    payload: DirectoryUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    row = db.get(Warehouse, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    return serialize_directory(patch_directory(db, row, payload, current_user.id))


@app.delete("/warehouses/{row_id}")
def remove_warehouse(
    row_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Warehouse, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    return delete_or_archive_warehouse(db, row, current_user.id)


@app.get("/agencies", response_model=list[DirectoryOut])
def get_agencies(
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DirectoryOut]:
    return [serialize_directory(row) for row in list_agencies(db, current_user.organization_id, include_archived)]


@app.post("/agencies", response_model=DirectoryOut, status_code=status.HTTP_201_CREATED)
def post_agency(
    payload: DirectoryCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    return serialize_directory(create_agency(db, payload, current_user.organization_id, current_user.id))


@app.patch("/agencies/{row_id}", response_model=DirectoryOut)
def patch_agency(
    row_id: UUID,
    payload: DirectoryUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    row = db.get(Agency, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agency not found")
    return serialize_directory(patch_directory(db, row, payload, current_user.id))


@app.delete("/agencies/{row_id}")
def remove_agency(
    row_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Agency, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agency not found")
    return delete_or_archive_agency(db, row, current_user.id)


@app.get("/vendors", response_model=list[DirectoryOut])
def get_vendors(
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DirectoryOut]:
    return [serialize_directory(row) for row in list_vendors(db, current_user.organization_id, include_archived)]


@app.post("/vendors", response_model=DirectoryOut, status_code=status.HTTP_201_CREATED)
def post_vendor(
    payload: DirectoryCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    return serialize_directory(create_vendor(db, payload, current_user.organization_id, current_user.id))


@app.patch("/vendors/{row_id}", response_model=DirectoryOut)
def patch_vendor(
    row_id: UUID,
    payload: DirectoryUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DirectoryOut:
    row = db.get(Vendor, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    return serialize_directory(patch_directory(db, row, payload, current_user.id))


@app.delete("/vendors/{row_id}")
def remove_vendor(
    row_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Vendor, row_id)
    if row is None or not can_access_organization(current_user, row.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    return delete_or_archive_vendor(db, row, current_user.id)


@app.get("/analytics", response_model=AnalyticsHub)
def get_analytics(
    asset_type: AssetType | None = Query(default=None),
    current_user: User = Depends(require_permission("view_analytics")),
    db: Session = Depends(get_db),
) -> AnalyticsHub:
    return build_analytics(db, asset_type, current_user.organization_id)


@app.get("/analytics/export")
def export_analytics_csv(
    table: str = Query(default="failures", description="failures | stock-alerts | efficiency"),
    asset_type: AssetType | None = Query(default=None),
    current_user: User = Depends(require_reporter),
    db: Session = Depends(get_db),
) -> Response:
    hub = build_analytics(db, asset_type, current_user.organization_id)
    return analytics_csv(hub, table)


# Preventive Maintenance Routes

@app.get("/pm/schedules", response_model=list[MaintenanceScheduleOut])
def get_pm_schedules(
    asset_id: UUID | None = Query(default=None),
    asset_type: AssetType | None = Query(default=None),
    pm_status: PMStatus | None = Query(default=None),
    location: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MaintenanceScheduleOut]:
    """List PM schedules with calculated status."""
    return list_pm_schedules_with_status(
        db, asset_id, asset_type, pm_status, location, current_user.organization_id, current_user
    )


@app.post("/pm/schedules", response_model=MaintenanceScheduleOut, status_code=status.HTTP_201_CREATED)
def create_pm_schedule(
    payload: MaintenanceScheduleCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> MaintenanceScheduleOut:
    """Create a new PM schedule for an asset."""
    asset = require_asset_access(db, current_user, payload.asset_id)
    
    schedule = MaintenanceSchedule(
        organization_id=asset.organization_id,
        asset_id=payload.asset_id,
        name=payload.name,
        description=payload.description,
        interval_miles=payload.interval_miles,
        interval_engine_hours=payload.interval_engine_hours,
        interval_days=payload.interval_days,
        interval_months=payload.interval_months,
        due_soon_threshold_miles=payload.due_soon_threshold_miles,
        due_soon_threshold_days=payload.due_soon_threshold_days,
        auto_create_work_order=payload.auto_create_work_order,
        created_by_id=current_user.id,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return serialize_pm_schedule(schedule, asset, db)


@app.get("/pm/schedules/{schedule_id}", response_model=MaintenanceScheduleOut)
def get_pm_schedule(
    schedule_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MaintenanceScheduleOut:
    """Get a specific PM schedule with calculated status."""
    schedule = require_pm_schedule_access(db, current_user, schedule_id)
    loaded = db.scalar(
        select(MaintenanceSchedule)
        .options(selectinload(MaintenanceSchedule.asset))
        .where(MaintenanceSchedule.id == schedule.id)
    )
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PM schedule not found")
    asset = loaded.asset or db.get(Asset, loaded.asset_id)
    return serialize_pm_schedule(loaded, asset, db)


@app.patch("/pm/schedules/{schedule_id}", response_model=MaintenanceScheduleOut)
def update_pm_schedule(
    schedule_id: UUID,
    payload: MaintenanceScheduleUpdate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> MaintenanceScheduleOut:
    """Update a PM schedule."""
    schedule = require_pm_schedule_access(db, current_user, schedule_id)
    db.refresh(schedule, ["asset"])  # Ensure asset is loaded for serialization
    
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(schedule, key, value)
    
    db.commit()
    db.refresh(schedule)
    return serialize_pm_schedule(schedule, schedule.asset, db)


@app.post("/pm/schedules/{schedule_id}/complete", response_model=MaintenanceScheduleOut)
def complete_pm_schedule(
    schedule_id: UUID,
    payload: PMCompletionRecord,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> MaintenanceScheduleOut:
    """Mark a PM schedule as completed and update next due."""
    schedule = require_pm_schedule_access(db, current_user, schedule_id)
    db.refresh(schedule, ["asset"])  # Ensure asset is loaded
    
    completed_at = payload.completed_at or datetime.now(UTC)
    
    # Record completion
    completion = PMCompletion(
        schedule_id=schedule.id,
        completed_at=completed_at,
        odometer_miles=payload.odometer_miles,
        engine_hours=payload.engine_hours,
        notes=payload.notes,
        created_by_id=current_user.id,
    )
    db.add(completion)
    
    # Update schedule last completed
    schedule.last_completed_date = completed_at
    if payload.odometer_miles:
        schedule.last_completed_miles = payload.odometer_miles
    if payload.engine_hours:
        schedule.last_completed_engine_hours = payload.engine_hours
    schedule.pending_work_order_id = None
    
    db.commit()
    db.refresh(schedule)
    return serialize_pm_schedule(schedule, schedule.asset, db)


@app.delete("/pm/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pm_schedule(
    schedule_id: UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> None:
    """Delete or deactivate a PM schedule."""
    schedule = require_pm_schedule_access(db, current_user, schedule_id)
    
    # Deactivate instead of delete if there are completions
    if schedule.completions:
        schedule.is_active = False
        db.commit()
    else:
        db.delete(schedule)
        db.commit()


# Meter Reading Routes

@app.get("/assets/{asset_id}/meters", response_model=list[MeterReadingOut])
def get_asset_meter_readings(
    asset_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MeterReadingOut]:
    """Get meter reading history for an asset."""
    asset = require_asset_access(db, current_user, asset_id)
    
    readings = db.scalars(
        select(MeterReading)
        .where(MeterReading.asset_id == asset_id)
        .order_by(MeterReading.reading_date.desc())
        .limit(limit)
    ).all()
    
    return [serialize_meter_reading(r) for r in readings]


@app.post("/assets/{asset_id}/meters", response_model=MeterReadingOut, status_code=status.HTTP_201_CREATED)
def create_meter_reading(
    asset_id: UUID,
    payload: MeterReadingCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> MeterReadingOut:
    """Record a new meter reading for an asset."""
    asset = require_asset_access(db, current_user, asset_id)
    
    reading_date = payload.reading_date or datetime.now(UTC)
    
    reading = record_meter_reading(
        db,
        asset_id,
        payload.odometer_miles,
        payload.engine_hours,
        reading_date,
        source="manual",
        notes=payload.notes,
        user_id=current_user.id,
    )
    return serialize_meter_reading(reading)


@app.get("/assets/{asset_id}/meters/latest", response_model=MeterReadingOut | None)
def get_latest_meter(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeterReadingOut | None:
    """Get the most recent meter reading for an asset."""
    asset = require_asset_access(db, current_user, asset_id)
    
    latest = get_latest_meter_reading(db, asset_id)
    return serialize_meter_reading(latest) if latest else None


# =============================================================================
# MAP / LOCATION API
# =============================================================================

@app.get("/map/assets")
def get_map_assets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all assets with locations for map display"""
    query = filter_assets_by_access(
        select(Asset).where(Asset.is_archived.is_(False)), current_user, db
    )
    assets = db.scalars(query).all()

    results = []
    for asset in assets:
        if is_retired(asset):
            continue
        loc = get_asset_location(db, asset)

        if loc.latitude and loc.longitude:
            status = effective_operational_status(asset)
            results.append({
                "id": str(asset.id),
                "vin": asset.vin,
                "make_model": asset.make_model,
                "asset_type": asset.asset_type.value if asset.asset_type else None,
                "operational_status": status.value if status else None,
                "latitude": float(loc.latitude),
                "longitude": float(loc.longitude),
                "location": loc.location_name,
                "location_source": loc.location_source.value,
                "updated_at": loc.location_timestamp.isoformat() if loc.location_timestamp else None,
                "warehouse_id": str(asset.warehouse_id) if asset.warehouse_id else None,
                "agency_id": str(asset.agency_id) if asset.agency_id else None,
            })
    
    return results


# ============================================================================
# User Management Endpoints
# ============================================================================

@app.get("/users", response_model=list[UserOut])
def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    """List all users in the current user's organization."""
    users = db.scalars(
        select(User)
        .where(User.organization_id == current_user.organization_id)
        .order_by(User.created_at.desc())
    ).all()
    return users


@app.get("/users/customers", response_model=list[UserOut])
def list_customer_users(
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
) -> list[UserOut]:
    """List customer users so fleet managers and admins can assign assets."""
    stmt = select(User).where(User.role == UserRole.CUSTOMER).order_by(User.full_name)
    if not is_system_admin(current_user):
        stmt = stmt.where(User.organization_id == current_user.organization_id)
    return list(db.scalars(stmt).all())


def _load_manageable_user(db: Session, actor: User, user_id: UUID) -> User:
    """Load a user the actor is allowed to administer.

    Guards two directions: the target must be inside the actor's organization,
    and the actor may not act on an account that outranks their own.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not is_system_admin(actor) and user.organization_id != actor.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    require_assignable_role(actor, user.role)
    return user


def _resolve_customer_agency(db: Session, actor: User, role: UserRole, agency_id: UUID | None) -> UUID | None:
    """Validate the agency scope for an account about to be saved.

    Customers must name exactly one agency inside the actor's organization;
    every other role carries none, so an account that stops being a customer
    also stops being agency-scoped.
    """
    if canonical_role(role) != UserRole.CUSTOMER:
        return None

    if agency_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Customer accounts must be assigned to an agency.",
        )

    agency = db.get(Agency, agency_id)
    if agency is None or agency.organization_id != actor.organization_id or agency.is_archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agency not found",
        )
    return agency.id


def _reject_protected_account(user: User, action: str) -> None:
    if user.email.lower() in settings.protected_admin_email_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This account is protected and cannot be {action}.",
        )


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    """Create a new user in the current user's organization."""
    require_assignable_role(current_user, payload.role)
    email = payload.email.strip().lower()

    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        organization_id=current_user.organization_id,
        agency_id=_resolve_customer_agency(db, current_user, payload.role, payload.agency_id),
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_active=True,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    """Update a user's information."""
    user = _load_manageable_user(db, current_user, user_id)
    is_self = user.id == current_user.id
    role_changing = payload.role is not None and payload.role != user.role
    agency_provided = "agency_id" in payload.model_fields_set

    if payload.role is not None and payload.role != user.role:
        require_assignable_role(current_user, payload.role)
        if is_self:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change your own role",
            )
        _reject_protected_account(user, "demoted")

    if payload.email is not None:
        email = payload.email.strip().lower()
        existing = db.scalar(select(User).where(func.lower(User.email) == email, User.id != user_id))
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        user.email = email

    if payload.password is not None:
        user.hashed_password = hash_password(payload.password)
        # Ends every session that was authenticated with the old password.
        user.password_changed_at = datetime.now(UTC)

    if payload.full_name is not None:
        user.full_name = payload.full_name

    if payload.role is not None:
        user.role = payload.role

    # Keep the agency scope consistent with the role: assign it, replace it, or
    # clear it when the account stops being a customer. Untouched accounts are
    # left alone so an unrelated edit does not fail on a legacy row.
    if role_changing or agency_provided:
        requested_agency = payload.agency_id if agency_provided else user.agency_id
        user.agency_id = _resolve_customer_agency(db, current_user, user.role, requested_agency)

    if payload.is_active is not None:
        if is_self and not payload.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account",
            )
        if not payload.is_active:
            _reject_protected_account(user, "deactivated")
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return user


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    """Delete a user."""
    user = _load_manageable_user(db, current_user, user_id)

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    _reject_protected_account(user, "deleted")

    db.delete(user)
    db.commit()


# =============================================================================
# ASSET AUTHORIZATION (Customer Asset Assignment)
# =============================================================================

@app.get("/users/{user_id}/asset-authorizations")
def get_user_asset_authorizations(
    user_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
):
    """Get list of assets authorized for a customer user."""
    _load_manageable_user(db, current_user, user_id)

    authorizations = db.execute(
        select(AssetAuthorization, Asset)
        .join(Asset, AssetAuthorization.asset_id == Asset.id)
        .where(AssetAuthorization.user_id == user_id)
    ).all()
    
    return [
        {
            "id": auth.id,
            "asset_id": str(auth.asset_id),
            "asset_vin": asset.vin,
            "asset_make_model": asset.make_model,
            "asset_type": asset.asset_type.value if asset.asset_type else None,
            "can_view": auth.can_view,
            "can_report_issue": auth.can_report_issue,
            "granted_at": auth.created_at,
        }
        for auth, asset in authorizations
    ]


@app.post("/users/{user_id}/asset-authorizations", status_code=status.HTTP_201_CREATED)
def grant_asset_authorization(
    user_id: UUID,
    payload: AssetAuthorizationGrant,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
):
    """Grant a customer user access to an asset."""
    user = _load_manageable_user(db, current_user, user_id)
    if not is_customer(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assets can only be assigned to customer users",
        )

    asset = require_asset_access(db, current_user, payload.asset_id)

    # A grant outside the customer's agency would be invisible to them anyway,
    # so refuse it here rather than leaving a misleading row behind.
    if user.agency_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This customer is not assigned to an agency yet. Set their agency before assigning assets.",
        )
    if asset.agency_id != user.agency_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That asset is not currently assigned to this customer's agency.",
        )

    existing = db.scalar(
        select(AssetAuthorization)
        .where(AssetAuthorization.user_id == user_id)
        .where(AssetAuthorization.asset_id == asset.id)
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset already authorized for this user")

    auth = AssetAuthorization(
        user_id=user_id,
        asset_id=asset.id,
        can_view=payload.can_view,
        can_report_issue=payload.can_report_issue,
        granted_by_id=current_user.id,
    )
    db.add(auth)
    db.commit()
    db.refresh(auth)
    
    return {
        "id": str(auth.id),
        "asset_id": str(auth.asset_id),
        "can_view": auth.can_view,
        "can_report_issue": auth.can_report_issue,
    }


@app.delete("/users/{user_id}/asset-authorizations/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_asset_authorization(
    user_id: UUID,
    asset_id: UUID,
    current_user: User = Depends(require_fleet_admin),
    db: Session = Depends(get_db),
):
    """Revoke a customer user's access to an asset."""
    _load_manageable_user(db, current_user, user_id)

    auth = db.scalar(
        select(AssetAuthorization)
        .where(AssetAuthorization.user_id == user_id)
        .where(AssetAuthorization.asset_id == asset_id)
    )
    if not auth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Authorization not found")
    
    db.delete(auth)
    db.commit()


@app.post("/auth/change-password", response_model=dict)
def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Allow users to change their own password."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password",
        )

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.password_changed_at = datetime.now(UTC)
    db.commit()

    return {"message": "Password changed successfully. Sign in again on your other devices."}
