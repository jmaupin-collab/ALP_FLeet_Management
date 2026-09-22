from app.models import AssetType

ALPR_TRAILER_CHECKLIST = [
    "Solar Panels",
    "ALPR Cameras",
    "Batteries",
    "Tires",
    "Chassis Frame",
]

VEHICLE_CHECKLIST = [
    "Engine",
    "Brakes",
    "Fluids",
    "Tires",
    "Lights",
    "Cabin",
]

LEMON_WARNING = (
    "⚠️ CRITICAL COST WARNING: Lifetime maintenance costs have exceeded replacement value. "
    "Flagged for immediate evaluation/retirement."
)


def checklist_for(asset_type: AssetType) -> list[str]:
    if asset_type == AssetType.ALPR_TRAILER:
        return list(ALPR_TRAILER_CHECKLIST)
    return list(VEHICLE_CHECKLIST)
