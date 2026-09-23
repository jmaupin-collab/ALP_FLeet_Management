from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Agency,
    Asset,
    AssetOperationalStatus,
    CustodyType,
    Deployment,
    DeploymentStatus,
    Inspection,
    InspectionResult,
    InspectionStatus,
    IssueSource,
    MaintenanceWorkOrder,
    User,
    Vendor,
    Warehouse,
    WorkOrderEventType,
    WorkOrderPriority,
    WorkOrderStatus,
)
from app.schemas import (
    AssetCreate,
    AssetUpdate,
    CustodyUpdate,
    DeploymentEnd,
    DeploymentStart,
    DirectoryCreate,
    DirectoryOut,
    DirectoryUpdate,
    EndDeploymentWorkflow,
    InspectionListOut,
    OperationalStatusUpdate,
    RepairCostAdd,
    WorkOrderCreate,
    WorkOrderUpdate,
)
from app.services import close_open_deployment, get_asset
from app.workorders import TERMINAL_WO, load_work_order, record_event


def require_not_archived(asset: Asset) -> None:
    if asset.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset is archived. Restore it before operational changes.")


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(record, user_id: UUID | None, *, creating: bool = False) -> None:
    record.updated_by_id = user_id
    if creating and getattr(record, "created_by_id", None) is None:
        record.created_by_id = user_id


def _unique_vin(db: Session, vin: str, exclude_id: UUID | None = None) -> str:
    value = vin.strip().upper()
    stmt = select(Asset).where(func.lower(Asset.vin) == value.lower())
    if exclude_id:
        stmt = stmt.where(Asset.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An asset with that VIN already exists")
    return value


def _apply_party_links(db: Session, asset: Asset, warehouse_id: UUID | None, agency_id: UUID | None, location: str) -> str:
    from app.services import require_directory_in_org

    if warehouse_id:
        warehouse = require_directory_in_org(db.get(Warehouse, warehouse_id), asset.organization_id, "Warehouse")
        asset.warehouse_id = warehouse.id
        asset.agency_id = None
        return warehouse.name
    if agency_id:
        agency = require_directory_in_org(db.get(Agency, agency_id), asset.organization_id, "Agency")
        asset.agency_id = agency.id
        asset.warehouse_id = None
        return agency.name
    return location


def create_asset(db: Session, payload: AssetCreate, user_id: UUID | None, commit: bool = True) -> Asset:
    """Create one asset. Pass commit=False to batch several into one transaction."""
    # Get user to obtain organization_id
    from app.models import User
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise ValueError("User not found")
    
    vin = _unique_vin(db, payload.vin)
    now = _now()
    
    # Set initial operational_status based on custody_type
    if payload.current_custody_type == CustodyType.CUSTOMER_AGENCY:
        operational_status = AssetOperationalStatus.DEPLOYED
    elif payload.current_custody_type == CustodyType.IN_TRANSIT:
        operational_status = AssetOperationalStatus.IN_TRANSIT
    else:  # WAREHOUSE_DEPOT or default
        operational_status = AssetOperationalStatus.AVAILABLE
    
    asset = Asset(
        vin=vin,
        license_plate=payload.license_plate,
        license_plate_state=payload.license_plate_state,
        make_model=payload.make_model.strip(),
        initial_purchase_cost=payload.initial_purchase_cost,
        current_location=payload.current_location.strip(),
        asset_type=payload.asset_type,
        current_custody_type=payload.current_custody_type,
        operational_status=operational_status,
        organization_id=user.organization_id,
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    location = _apply_party_links(db, asset, payload.warehouse_id, payload.agency_id, payload.current_location.strip())
    asset.current_location = location
    db.add(asset)
    db.flush()
    
    # Only create deployment if not at warehouse depot
    if payload.current_custody_type != CustodyType.WAREHOUSE_DEPOT:
        status_value = DeploymentStatus.STAGED
        if payload.current_custody_type == CustodyType.IN_TRANSIT:
            status_value = DeploymentStatus.IN_TRANSIT
        elif payload.current_custody_type == CustodyType.CUSTOMER_AGENCY:
            status_value = DeploymentStatus.DEPLOYED
        db.add(
            Deployment(
                asset_id=asset.id,
                organization_id=user.organization_id,
                location=asset.current_location,
                status=status_value,
                custody_type=payload.current_custody_type,
                started_at=now,
                notes=payload.notes or "Asset deployed.",
                created_by_id=user_id,
                updated_by_id=user_id,
            )
        )
    
    if commit:
        db.commit()
    else:
        db.flush()
    return get_asset(db, asset.id)


_WAREHOUSE_EDITABLE_STATUSES = {
    AssetOperationalStatus.AVAILABLE,
    AssetOperationalStatus.MAINTENANCE,
    AssetOperationalStatus.OUT_OF_SERVICE,
    AssetOperationalStatus.RETIRED,
}


def _as_retired_status(target: AssetOperationalStatus) -> AssetOperationalStatus:
    if target == AssetOperationalStatus.OUT_OF_SERVICE:
        return AssetOperationalStatus.RETIRED
    return target


def set_operational_status(db: Session, asset: Asset, payload: OperationalStatusUpdate, user_id: UUID | None) -> Asset:
    """Correct fleet status from the asset page. Updates dashboard, map, and lists."""
    require_not_archived(asset)
    target = payload.operational_status
    if target in (AssetOperationalStatus.DEPLOYED, AssetOperationalStatus.IN_TRANSIT):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Use Start Deployment or Move / Transfer to set Deployed or In Transit.",
        )
    if target not in _WAREHOUSE_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid operational status: {target}",
        )
    target = _as_retired_status(target)

    now = _now()
    open_deployment = db.scalar(
        select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None))
    )
    if open_deployment:
        open_deployment.ended_at = now
        open_deployment.status = DeploymentStatus.COMPLETED
        open_deployment.end_reason = f"status:{target.value}"
        open_deployment.completion_notes = payload.notes
        open_deployment.completed_by_id = user_id
        open_deployment.updated_by_id = user_id

    warehouse = None
    warehouse_id = payload.warehouse_id or asset.warehouse_id
    if warehouse_id:
        from app.services import require_directory_in_org

        warehouse = require_directory_in_org(db.get(Warehouse, warehouse_id), asset.organization_id, "Warehouse")
        asset.warehouse_id = warehouse.id

    if target == AssetOperationalStatus.AVAILABLE:
        location = warehouse.name if warehouse else (asset.current_location or "Warehouse")
    elif target == AssetOperationalStatus.MAINTENANCE:
        location = f"{warehouse.name} (Maintenance)" if warehouse else "Maintenance Facility"
    else:
        location = f"{warehouse.name} (Retired)" if warehouse else "Retired"

    asset.current_location = location
    asset.current_custody_type = CustodyType.WAREHOUSE_DEPOT
    asset.agency_id = None
    asset.carrier_name = None
    asset.tracking_code = None
    asset.operational_status = target
    asset.updated_by_id = user_id

    db.add(
        Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=location,
            status=DeploymentStatus.COMPLETED,
            custody_type=CustodyType.WAREHOUSE_DEPOT,
            started_at=now,
            ended_at=now,
            notes=payload.notes or f"Operational status set to {target.value.replace('_', ' ')}",
            created_by_id=user_id,
        )
    )

    from app.services import reconcile_asset_deployment_state

    reconcile_asset_deployment_state(db, asset)
    db.commit()
    return get_asset(db, asset.id)


def update_asset_details(db: Session, asset: Asset, payload: AssetUpdate, user_id: UUID | None) -> Asset:
    from app.services import require_directory_in_org

    data = payload.model_dump(exclude_unset=True)
    if "vin" in data and data["vin"]:
        asset.vin = _unique_vin(db, data["vin"], exclude_id=asset.id)
    # Sending an explicit null clears the plate, so presence is what matters.
    if "license_plate" in data:
        asset.license_plate = data["license_plate"]
    if "license_plate_state" in data:
        asset.license_plate_state = data["license_plate_state"]
    if "make_model" in data and data["make_model"]:
        asset.make_model = data["make_model"].strip()
    if "initial_purchase_cost" in data and data["initial_purchase_cost"] is not None:
        asset.initial_purchase_cost = data["initial_purchase_cost"]
    if "asset_type" in data and data["asset_type"] is not None:
        asset.asset_type = data["asset_type"]
    linking = "warehouse_id" in data or "agency_id" in data
    if linking and asset.operational_status in {AssetOperationalStatus.DEPLOYED, AssetOperationalStatus.IN_TRANSIT}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use Move / Transfer or Return to warehouse to change agency or warehouse while the asset is deployed or in transit.",
        )
    if "warehouse_id" in data:
        warehouse_id = data["warehouse_id"]
        if warehouse_id:
            require_directory_in_org(db.get(Warehouse, warehouse_id), asset.organization_id, "Warehouse")
        asset.warehouse_id = warehouse_id
    if "agency_id" in data:
        agency_id = data["agency_id"]
        if agency_id:
            require_directory_in_org(db.get(Agency, agency_id), asset.organization_id, "Agency")
        asset.agency_id = agency_id
    _stamp(asset, user_id)
    db.commit()
    return get_asset(db, asset.id)


def archive_asset(db: Session, asset: Asset, user_id: UUID | None, archived: bool) -> Asset:
    asset.is_archived = archived
    asset.archived_at = _now() if archived else None
    _stamp(asset, user_id)
    if archived:
        close_open_deployment(db, asset)
        open_row = db.scalar(select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None)))
        if open_row is None:
            db.add(
                Deployment(
                    asset_id=asset.id,
                    organization_id=asset.organization_id,
                    location=asset.current_location,
                    status=DeploymentStatus.STORED,
                    custody_type=CustodyType.WAREHOUSE_DEPOT,
                    started_at=_now(),
                    ended_at=_now(),
                    notes="Asset retired / archived.",
                    created_by_id=user_id,
                    updated_by_id=user_id,
                )
            )
    db.commit()
    return get_asset(db, asset.id)


def asset_has_operational_history(db: Session, asset: Asset) -> bool:
    wo_count = db.scalar(select(func.count()).select_from(MaintenanceWorkOrder).where(MaintenanceWorkOrder.asset_id == asset.id)) or 0
    insp_count = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.asset_id == asset.id)) or 0
    dep_count = db.scalar(select(func.count()).select_from(Deployment).where(Deployment.asset_id == asset.id)) or 0
    return wo_count > 0 or insp_count > 0 or dep_count > 1


def delete_asset(db: Session, asset: Asset, force: bool = False) -> None:
    """Delete an asset. If force=True (admin), bypass operational history check."""
    if not force and asset_has_operational_history(db, asset):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This asset has lifecycle history. Archive it instead of deleting so analytics and the timeline stay intact.",
        )
    db.delete(asset)
    db.commit()


def start_deployment(db: Session, asset: Asset, payload: DeploymentStart, user_id: UUID | None) -> Asset:
    require_not_archived(asset)
    agency_id = payload.agency_id
    typed_location = (payload.location or "").strip()
    if payload.custody_type == CustodyType.CUSTOMER_AGENCY and agency_id is None:
        from app.location import resolve_agency_by_location
        matched = resolve_agency_by_location(db, asset.organization_id, typed_location)
        if matched:
            agency_id = matched.id
    # A linked warehouse or agency names the location; the typed string is only
    # the fallback for a site that is not in the directory.
    location = _apply_party_links(db, asset, payload.warehouse_id, agency_id, typed_location)
    apply_from_payload = CustodyUpdate(
        custody_type=payload.custody_type,
        location=location,
        carrier_name=payload.carrier_name,
        tracking_code=payload.tracking_code,
        notes=payload.notes or "Deployment started.",
        warehouse_id=payload.warehouse_id,
        agency_id=agency_id,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    from app.services import apply_custody

    apply_custody(db, asset, apply_from_payload, user_id)

    loaded = get_asset(db, asset.id)
    open_row = next((row for row in loaded.deployments if row.ended_at is None), None)
    if open_row:
        # Default DeploymentStart.status is "deployed"; do not clobber an in-transit move.
        if payload.custody_type == CustodyType.IN_TRANSIT:
            open_row.status = DeploymentStatus.IN_TRANSIT
            loaded.operational_status = AssetOperationalStatus.IN_TRANSIT
        elif payload.status != open_row.status:
            open_row.status = payload.status
        db.commit()
    return get_asset(db, asset.id)


def end_deployment(db: Session, asset: Asset, payload: DeploymentEnd, user_id: UUID | None) -> Asset:
    require_not_archived(asset)
    open_row = db.scalar(select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None)))
    if open_row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No open deployment to end")
    location = payload.location.strip() if payload.location else asset.current_location
    if payload.warehouse_id:
        warehouse = db.get(Warehouse, payload.warehouse_id)
        if warehouse is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
        location = warehouse.name
        asset.warehouse_id = warehouse.id
        asset.agency_id = None
    apply_from_payload = CustodyUpdate(
        custody_type=CustodyType.WAREHOUSE_DEPOT,
        location=location,
        notes=payload.notes or "Deployment ended; unit returned to depot.",
        warehouse_id=payload.warehouse_id,
    )
    from app.services import apply_custody

    apply_custody(db, asset, apply_from_payload, user_id)
    loaded = get_asset(db, asset.id)
    loaded.operational_status = AssetOperationalStatus.AVAILABLE
    loaded.updated_by_id = user_id
    db.commit()
    return get_asset(db, asset.id)


def update_deployment_notes(db: Session, deployment_id: UUID, notes: str | None, location: str | None, user_id: UUID | None) -> Deployment:
    row = db.scalar(select(Deployment).options(selectinload(Deployment.asset)).where(Deployment.id == deployment_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    if notes is not None:
        row.notes = notes
    if location is not None:
        if row.ended_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Closed deployment locations are historical. Start a transfer to change current location.",
            )
        row.location = location.strip()
        row.asset.current_location = location.strip()
    row.updated_by_id = user_id
    db.commit()
    db.refresh(row)
    return row


def end_deployment_workflow(db: Session, asset: Asset, payload: EndDeploymentWorkflow, user_id: UUID | None) -> Asset:
    """
    Complete end deployment workflow with next asset disposition.
    Handles: Return to Warehouse, Transfer to Agency, In Transit, Maintenance, Retired.
    Uses a single transaction to ensure data integrity.
    """
    require_not_archived(asset)
    
    # Find the active deployment
    open_deployment = db.scalar(
        select(Deployment).where(Deployment.asset_id == asset.id, Deployment.ended_at.is_(None))
    )
    if open_deployment is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active deployment to end"
        )
    
    now = datetime.now(UTC)
    
    # Close the current deployment (history is preserved)
    open_deployment.ended_at = now
    open_deployment.status = DeploymentStatus.COMPLETED
    open_deployment.end_reason = "Transfer" if payload.disposition == "transfer_to_agency" else payload.disposition
    open_deployment.completion_notes = payload.completion_notes
    open_deployment.completed_by_id = user_id
    open_deployment.updated_by_id = user_id
    
    # Handle each disposition type
    if payload.disposition == "return_to_warehouse":
        if not payload.destination_warehouse_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="destination_warehouse_id required for return_to_warehouse disposition"
            )
        from app.services import require_directory_in_org

        warehouse = require_directory_in_org(
            db.get(Warehouse, payload.destination_warehouse_id), asset.organization_id, "Warehouse"
        )
        
        # Update asset
        asset.current_location = warehouse.name
        asset.current_custody_type = CustodyType.WAREHOUSE_DEPOT
        asset.warehouse_id = warehouse.id
        asset.agency_id = None
        asset.carrier_name = None
        asset.tracking_code = None
        asset.operational_status = AssetOperationalStatus.AVAILABLE if payload.set_available else AssetOperationalStatus.AVAILABLE
        asset.updated_by_id = user_id
        
        # Create new custody record
        new_deployment = Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=warehouse.name,
            status=DeploymentStatus.COMPLETED,
            custody_type=CustodyType.WAREHOUSE_DEPOT,
            started_at=now,
            ended_at=now,
            notes=f"Returned from deployment. {payload.completion_notes or ''}".strip(),
            created_by_id=user_id,
        )
        db.add(new_deployment)
    
    elif payload.disposition == "transfer_to_agency":
        if not payload.next_agency_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="next_agency_id required for transfer_to_agency disposition"
            )
        from app.services import require_directory_in_org

        agency = require_directory_in_org(db.get(Agency, payload.next_agency_id), asset.organization_id, "Agency")
        
        # Update asset
        asset.current_location = payload.next_deployment_location or agency.name
        asset.current_custody_type = CustodyType.CUSTOMER_AGENCY
        asset.agency_id = agency.id
        asset.warehouse_id = None
        asset.carrier_name = None
        asset.tracking_code = None
        asset.operational_status = AssetOperationalStatus.DEPLOYED
        asset.updated_by_id = user_id
        
        # Keep an open deployment so the asset stays on the Deployments list.
        new_deployment = Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=payload.next_deployment_location or agency.name,
            status=DeploymentStatus.DEPLOYED,
            custody_type=CustodyType.CUSTOMER_AGENCY,
            started_at=now,
            notes=payload.next_deployment_notes or f"Transfer from previous deployment to {agency.name}",
            created_by_id=user_id,
        )
        from app.location import apply_location_snapshot
        apply_location_snapshot(
            db,
            new_deployment,
            asset,
            agency_id=agency.id,
            location=payload.next_deployment_location or agency.name,
            address=payload.next_deployment_location or agency.address,
        )
        db.add(new_deployment)
    
    elif payload.disposition == "in_transit":
        if not payload.carrier_name or not payload.tracking_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="carrier_name and tracking_code required for in_transit disposition"
            )
        if not payload.transit_destination:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="transit_destination required for in_transit disposition"
            )
        
        # Update asset
        asset.current_location = f"In Transit: {payload.transit_origin or 'Unknown'} → {payload.transit_destination}"
        asset.current_custody_type = CustodyType.IN_TRANSIT
        asset.carrier_name = payload.carrier_name
        asset.tracking_code = payload.tracking_code
        asset.operational_status = AssetOperationalStatus.IN_TRANSIT
        asset.updated_by_id = user_id
        
        # Create in-transit deployment
        new_deployment = Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=asset.current_location,
            status=DeploymentStatus.IN_TRANSIT,
            custody_type=CustodyType.IN_TRANSIT,
            carrier_name=payload.carrier_name,
            tracking_code=payload.tracking_code,
            started_at=now,
            notes=payload.completion_notes or f"In transit from {payload.transit_origin or 'deployment'} to {payload.transit_destination}",
            created_by_id=user_id,
        )
        from app.location import apply_location_snapshot
        apply_location_snapshot(
            db,
            new_deployment,
            asset,
            location=payload.transit_destination,
            address=payload.transit_destination,
        )
        db.add(new_deployment)
    
    elif payload.disposition == "maintenance":
        warehouse_location = "Maintenance Facility"
        if payload.maintenance_warehouse_id:
            warehouse = db.get(Warehouse, payload.maintenance_warehouse_id)
            if warehouse:
                warehouse_location = f"{warehouse.name} (Maintenance)"
                asset.warehouse_id = warehouse.id
        
        # Update asset
        asset.current_location = warehouse_location
        asset.current_custody_type = CustodyType.WAREHOUSE_DEPOT
        asset.agency_id = None
        asset.carrier_name = None
        asset.tracking_code = None
        asset.operational_status = AssetOperationalStatus.MAINTENANCE
        asset.updated_by_id = user_id
        
        # Create custody record
        new_deployment = Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=warehouse_location,
            status=DeploymentStatus.COMPLETED,
            custody_type=CustodyType.WAREHOUSE_DEPOT,
            started_at=now,
            ended_at=now,
            notes=f"Sent to maintenance. {payload.completion_notes or ''}".strip(),
            created_by_id=user_id,
        )
        db.add(new_deployment)
        
        # Create work order if requested
        if payload.create_work_order:
            wo = MaintenanceWorkOrder(
                organization_id=asset.organization_id,
                asset_id=asset.id,
                title=payload.work_order_title or "Post-deployment maintenance",
                description=payload.work_order_description,
                status=WorkOrderStatus.OPEN,
                opened_at=now,
                issue_source=IssueSource.MANUAL_REPORT,
                priority=WorkOrderPriority.MEDIUM,
                created_by_id=user_id,
            )
            db.add(wo)
            db.flush()
            record_event(db, wo, WorkOrderEventType.CREATED, user_id, None, None, "Work order created from deployment end workflow")
    
    elif payload.disposition in {"retired", "out_of_service"}:
        from app.services import require_directory_in_org

        warehouse = None
        warehouse_id = payload.destination_warehouse_id or payload.maintenance_warehouse_id or asset.warehouse_id
        if warehouse_id:
            warehouse = require_directory_in_org(db.get(Warehouse, warehouse_id), asset.organization_id, "Warehouse")
            asset.warehouse_id = warehouse.id
        location = f"{warehouse.name} (Retired)" if warehouse else "Retired"
        asset.current_location = location
        asset.current_custody_type = CustodyType.WAREHOUSE_DEPOT
        asset.agency_id = None
        asset.carrier_name = None
        asset.tracking_code = None
        asset.operational_status = AssetOperationalStatus.RETIRED
        asset.updated_by_id = user_id
        reason = payload.out_of_service_reason or "Retired"
        new_deployment = Deployment(
            organization_id=asset.organization_id,
            asset_id=asset.id,
            location=location,
            status=DeploymentStatus.COMPLETED,
            custody_type=CustodyType.WAREHOUSE_DEPOT,
            started_at=now,
            ended_at=now,
            notes=f"{reason}. {payload.completion_notes or ''}".strip(),
            created_by_id=user_id,
        )
        db.add(new_deployment)
    
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid disposition: {payload.disposition}"
        )

    from app.services import reconcile_asset_deployment_state
    reconcile_asset_deployment_state(db, asset)
    db.commit()
    return get_asset(db, asset.id)


def cancel_in_progress_inspection(db: Session, inspection: Inspection, user_id: UUID | None) -> Inspection:
    if inspection.status != InspectionStatus.IN_PROGRESS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only in-progress inspections can be cancelled")
    inspection.status = InspectionStatus.CANCELLED
    inspection.updated_by_id = user_id
    db.commit()
    db.refresh(inspection)
    return inspection


def list_inspections(db: Session, current_user) -> list[InspectionListOut]:
    from app.rbac import get_user_asset_ids, is_customer, is_system_admin

    stmt = (
        select(Inspection)
        .options(selectinload(Inspection.asset), selectinload(Inspection.items), selectinload(Inspection.photos))
        .order_by(Inspection.started_at.desc())
    )
    if not is_system_admin(current_user):
        stmt = stmt.where(Inspection.organization_id == current_user.organization_id)
    if is_customer(current_user):
        authorized = get_user_asset_ids(db, current_user)
        if not authorized:
            return []
        stmt = stmt.where(Inspection.asset_id.in_(authorized))

    rows = db.scalars(stmt).all()
    out: list[InspectionListOut] = []
    for row in rows:
        fails = sum(1 for item in row.items if item.result == InspectionResult.FAIL)
        out.append(
            InspectionListOut(
                id=row.id,
                asset_id=row.asset_id,
                asset=row.asset.make_model if row.asset else "",
                asset_type=row.asset.asset_type,
                status=row.status,
                started_at=row.started_at,
                submitted_at=row.submitted_at,
                notes=row.notes,
                fail_count=fails,
                source=getattr(row, "source", None) or "internal",
                photo_count=len(row.photos or []),
            )
        )
    return out


def create_work_order(db: Session, payload: WorkOrderCreate, user_id: UUID | None) -> MaintenanceWorkOrder:
    asset = get_asset(db, payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    require_not_archived(asset)
    now = _now()
    wo = MaintenanceWorkOrder(
        asset_id=payload.asset_id,
        organization_id=asset.organization_id,
        title=payload.title.strip(),
        description=payload.description,
        status=payload.status,
        opened_at=now,
        downtime_start=now if payload.status not in TERMINAL_WO else None,
        closed_at=now if payload.status in TERMINAL_WO else None,
        downtime_hours=payload.downtime_hours,
        labor_cost=payload.labor_cost,
        parts_cost=payload.parts_cost,
        vendor_invoice_cost=payload.vendor_invoice_cost,
        failed_component=payload.failed_component,
        repair_channel=payload.repair_channel,
        vendor_name=payload.vendor_name,
        issue_source=payload.issue_source or IssueSource.MANUAL_REPORT,
        priority=payload.priority or WorkOrderPriority.MEDIUM,
        assigned_to_id=payload.assigned_to_id,
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    db.add(wo)
    db.flush()
    record_event(db, wo, WorkOrderEventType.CREATED, user_id, new=wo.status.value, notes=payload.description)
    if payload.assigned_to_id:
        record_event(db, wo, WorkOrderEventType.TECHNICIAN_ASSIGNED, user_id, new=str(payload.assigned_to_id))
    db.commit()
    return load_work_order(db, wo.id)


def _clear_maintenance_if_unassigned(asset: Asset | None, user_id: UUID | None) -> None:
    """Completing a WO must not pull a customer-deployed or in-transit asset off that assignment."""
    if asset is None:
        return
    if asset.operational_status != AssetOperationalStatus.MAINTENANCE:
        return
    asset.operational_status = AssetOperationalStatus.AVAILABLE
    asset.updated_by_id = user_id


def update_work_order(db: Session, wo: MaintenanceWorkOrder, payload: WorkOrderUpdate, user_id: UUID | None) -> MaintenanceWorkOrder:
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None and data["status"] != wo.status:
        previous = wo.status.value
        wo.status = data["status"]
        if wo.status in TERMINAL_WO and wo.closed_at is None:
            wo.closed_at = _now()
            wo.downtime_end = wo.downtime_end or wo.closed_at
        if wo.status not in TERMINAL_WO:
            wo.closed_at = None
        record_event(db, wo, WorkOrderEventType.STATUS_CHANGED, user_id, previous=previous, new=wo.status.value)
        if wo.status == WorkOrderStatus.COMPLETED:
            _clear_maintenance_if_unassigned(db.get(Asset, wo.asset_id), user_id)
            record_event(db, wo, WorkOrderEventType.COMPLETED, user_id, new=wo.status.value)
    if "assigned_to_id" in data and data["assigned_to_id"] != wo.assigned_to_id:
        previous = str(wo.assigned_to_id) if wo.assigned_to_id else None
        wo.assigned_to_id = data["assigned_to_id"]
        record_event(
            db,
            wo,
            WorkOrderEventType.TECHNICIAN_ASSIGNED,
            user_id,
            previous=previous,
            new=str(wo.assigned_to_id) if wo.assigned_to_id else None,
        )
    if "vendor_name" in data and data["vendor_name"] != wo.vendor_name:
        record_event(db, wo, WorkOrderEventType.VENDOR_ASSIGNED, user_id, previous=wo.vendor_name, new=data["vendor_name"])
        wo.vendor_name = data["vendor_name"].strip() if isinstance(data["vendor_name"], str) and data["vendor_name"] else data["vendor_name"]
    for field in ("investigation_notes", "repair_actions", "completion_notes", "description"):
        if field in data and data[field] and data[field] != getattr(wo, field):
            record_event(db, wo, WorkOrderEventType.NOTES_ADDED, user_id, new=data[field][:240])
    if "parts_used" in data and data["parts_used"] and data["parts_used"] != wo.parts_used:
        record_event(db, wo, WorkOrderEventType.PARTS_ADDED, user_id, previous=wo.parts_used, new=data["parts_used"])
    if any(key in data for key in ("root_cause_category", "root_cause_description", "corrective_action", "preventive_action")):
        if data.get("root_cause_category") or data.get("root_cause_description"):
            record_event(
                db,
                wo,
                WorkOrderEventType.ROOT_CAUSE_ENTERED,
                user_id,
                new=str(data.get("root_cause_category") or wo.root_cause_category or ""),
                notes=data.get("root_cause_description"),
            )
    for field in (
        "title",
        "description",
        "failed_component",
        "investigation_notes",
        "repair_actions",
        "parts_used",
        "root_cause_description",
        "corrective_action",
        "preventive_action",
        "completion_notes",
    ):
        if field in data:
            value = data[field]
            setattr(wo, field, value.strip() if isinstance(value, str) else value)
    for field in (
        "labor_cost",
        "parts_cost",
        "vendor_invoice_cost",
        "downtime_hours",
        "labor_hours",
        "repair_channel",
        "issue_source",
        "priority",
        "downtime_start",
        "downtime_end",
        "root_cause_category",
        "is_archived",
    ):
        if field in data and data[field] is not None:
            setattr(wo, field, data[field])
    wo.updated_by_id = user_id
    db.commit()
    return load_work_order(db, wo.id)


def add_repair_cost(db: Session, wo: MaintenanceWorkOrder, payload: RepairCostAdd, user_id: UUID | None) -> MaintenanceWorkOrder:
    previous = str(Decimal(wo.labor_cost) + Decimal(wo.parts_cost) + Decimal(wo.vendor_invoice_cost or 0))
    wo.labor_cost = Decimal(wo.labor_cost) + payload.labor_cost
    wo.parts_cost = Decimal(wo.parts_cost) + payload.parts_cost
    wo.vendor_invoice_cost = Decimal(wo.vendor_invoice_cost or 0) + payload.vendor_invoice_cost
    if payload.notes:
        extra = f"Cost added: {payload.notes}"
        wo.description = f"{wo.description}\n{extra}".strip() if wo.description else extra
    wo.updated_by_id = user_id
    record_event(
        db,
        wo,
        WorkOrderEventType.REPAIR_COST_ADDED,
        user_id,
        previous=previous,
        new=str(Decimal(wo.labor_cost) + Decimal(wo.parts_cost) + Decimal(wo.vendor_invoice_cost or 0)),
        notes=payload.notes,
    )
    db.commit()
    return load_work_order(db, wo.id)


def close_work_order(db: Session, wo: MaintenanceWorkOrder, user_id: UUID | None) -> MaintenanceWorkOrder:
    if wo.status in TERMINAL_WO:
        return load_work_order(db, wo.id) or wo
    previous = wo.status.value
    wo.status = WorkOrderStatus.COMPLETED
    wo.closed_at = _now()
    wo.downtime_end = wo.downtime_end or wo.closed_at
    wo.updated_by_id = user_id
    
    _clear_maintenance_if_unassigned(db.get(Asset, wo.asset_id), user_id)
    
    record_event(db, wo, WorkOrderEventType.STATUS_CHANGED, user_id, previous=previous, new=wo.status.value)
    record_event(db, wo, WorkOrderEventType.COMPLETED, user_id, new=wo.status.value)
    db.commit()
    return load_work_order(db, wo.id)


def delete_work_order(db: Session, wo: MaintenanceWorkOrder) -> None:
    if wo.status == WorkOrderStatus.COMPLETED or wo.inspection_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed or inspection-linked work orders cannot be deleted. Cancel or archive them instead.",
        )
    has_cost = Decimal(wo.labor_cost) + Decimal(wo.parts_cost) + Decimal(wo.vendor_invoice_cost or 0) > 0
    if has_cost or wo.status not in {WorkOrderStatus.OPEN, WorkOrderStatus.CANCELLED}:
        wo.status = WorkOrderStatus.CANCELLED
        wo.is_archived = True
        wo.closed_at = wo.closed_at or _now()
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Historical work orders were cancelled/archived instead of deleted.",
        )
    db.delete(wo)
    db.commit()


def serialize_directory(row) -> DirectoryOut:
    return DirectoryOut.model_validate(row)


def list_warehouses(db: Session, organization_id: UUID, include_archived: bool = False) -> list[Warehouse]:
    # Directory lists are scoped to the caller's organization_id, including System Admin
    # (no org-switcher; System Admin operates in their assigned organization context).
    stmt = select(Warehouse).where(Warehouse.organization_id == organization_id).order_by(Warehouse.name)
    if not include_archived:
        stmt = stmt.where(Warehouse.is_archived == False)
    return list(db.scalars(stmt).all())


def list_agencies(db: Session, organization_id: UUID, include_archived: bool = False) -> list[Agency]:
    stmt = select(Agency).where(Agency.organization_id == organization_id).order_by(Agency.name)
    if not include_archived:
        stmt = stmt.where(Agency.is_archived == False)
    return list(db.scalars(stmt).all())


def list_vendors(db: Session, organization_id: UUID, include_archived: bool = False) -> list[Vendor]:
    stmt = select(Vendor).where(Vendor.organization_id == organization_id).order_by(Vendor.name)
    if not include_archived:
        stmt = stmt.where(Vendor.is_archived == False)
    return list(db.scalars(stmt).all())


def create_warehouse(db: Session, payload: DirectoryCreate, organization_id: UUID, user_id: UUID | None) -> Warehouse:
    from app.geocoding import geocode_directory_address

    latitude, longitude = geocode_directory_address(payload.address, label="Warehouse")

    row = Warehouse(
        organization_id=organization_id,
        name=payload.name.strip(),
        address=payload.address,
        latitude=latitude,
        longitude=longitude,
        created_by_id=user_id,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Warehouse name already exists in your organization") from exc
    db.refresh(row)
    return row


def create_agency(db: Session, payload: DirectoryCreate, organization_id: UUID, user_id: UUID | None) -> Agency:
    from app.geocoding import geocode_directory_address

    # Same helper as warehouses: deployments to this agency need reliable
    # coordinates for the map fallback.
    latitude, longitude = geocode_directory_address(payload.address, label="Agency")

    row = Agency(
        organization_id=organization_id,
        name=payload.name.strip(),
        agency_type=payload.agency_type or "Law Enforcement",
        contact_name=payload.contact_name,
        address=payload.address,
        latitude=latitude,
        longitude=longitude,
        created_by_id=user_id,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agency name already exists in your organization") from exc
    db.refresh(row)
    return row


def create_vendor(db: Session, payload: DirectoryCreate, organization_id: UUID, user_id: UUID | None) -> Vendor:
    row = Vendor(
        organization_id=organization_id,
        name=payload.name.strip(),
        specialty=payload.specialty,
        created_by_id=user_id,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vendor name already exists in your organization") from exc
    db.refresh(row)
    return row


def patch_directory(db: Session, row, payload: DirectoryUpdate, user_id: UUID | None):
    data = payload.model_dump(exclude_unset=True)
    
    # Re-geocode when the address changes. Vendors carry no coordinates.
    if "address" in data and hasattr(row, "latitude"):
        from app.geocoding import geocode_directory_address

        row.latitude, row.longitude = geocode_directory_address(
            data["address"],
            label=type(row).__name__,
            existing=(row.latitude, row.longitude),
        )


    for key, value in data.items():
        if hasattr(row, key):
            setattr(row, key, value.strip() if isinstance(value, str) else value)
    row.updated_by_id = user_id
    db.commit()
    db.refresh(row)
    return row


def _referenced_warehouse(db: Session, warehouse: Warehouse) -> bool:
    return bool(db.scalar(select(Asset.id).where(or_(Asset.warehouse_id == warehouse.id, Asset.current_location == warehouse.name)).limit(1)))


def _referenced_agency(db: Session, agency: Agency) -> bool:
    return bool(
        db.scalar(
            select(Asset.id).where(or_(Asset.agency_id == agency.id, Asset.current_location.ilike(f"%{agency.name}%"))).limit(1)
        )
    )


def _referenced_vendor(db: Session, vendor: Vendor) -> bool:
    return bool(db.scalar(select(MaintenanceWorkOrder.id).where(MaintenanceWorkOrder.vendor_name == vendor.name).limit(1)))


def delete_or_archive_warehouse(db: Session, warehouse: Warehouse, user_id: UUID | None) -> dict:
    if _referenced_warehouse(db, warehouse):
        warehouse.is_archived = True
        warehouse.updated_by_id = user_id
        db.commit()
        return {"action": "archived", "detail": "Warehouse is referenced by assets and was archived instead of deleted."}
    db.delete(warehouse)
    db.commit()
    return {"action": "deleted"}


def delete_or_archive_agency(db: Session, agency: Agency, user_id: UUID | None) -> dict:
    if _referenced_agency(db, agency):
        agency.is_archived = True
        agency.updated_by_id = user_id
        db.commit()
        return {"action": "archived", "detail": "Agency is referenced by assets and was archived instead of deleted."}
    db.delete(agency)
    db.commit()
    return {"action": "deleted"}


def delete_or_archive_vendor(db: Session, vendor: Vendor, user_id: UUID | None) -> dict:
    if _referenced_vendor(db, vendor):
        vendor.is_archived = True
        vendor.updated_by_id = user_id
        db.commit()
        return {"action": "archived", "detail": "Vendor is referenced by work orders and was archived instead of deleted."}
    db.delete(vendor)
    db.commit()
    return {"action": "deleted"}


def seed_safe_user_fields(user: User) -> User:
    return user
