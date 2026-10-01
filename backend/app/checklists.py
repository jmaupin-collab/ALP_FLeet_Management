from app.models import AssetType

TRAILER_CHECKLIST = [
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


# Split by what is physically there to inspect, not by how the type is reported
# elsewhere: a towed unit has no engine, brakes or cabin to check.
TRAILER_TYPES = frozenset({AssetType.ALPR_TRAILER, AssetType.ATP, AssetType.SKY_CARRIER})


def checklist_for(asset_type: AssetType) -> list[str]:
    if asset_type in TRAILER_TYPES:
        return list(TRAILER_CHECKLIST)
    return list(VEHICLE_CHECKLIST)
