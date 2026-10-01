"""Inspection checklists follow what is physically on the asset.

Towed units get the trailer inspection; anything with a cab and an engine gets
the vehicle one. Every asset type has to land on one side or the other, so a
type added later cannot quietly inherit the wrong inspection.
"""

import pytest

from app.checklists import TRAILER_CHECKLIST, VEHICLE_CHECKLIST, checklist_for
from app.models import AssetType


@pytest.mark.parametrize(
    "asset_type", [AssetType.ALPR_TRAILER, AssetType.ATP, AssetType.SKY_CARRIER]
)
def test_towed_units_get_the_trailer_inspection(asset_type):
    assert checklist_for(asset_type) == TRAILER_CHECKLIST


@pytest.mark.parametrize("asset_type", [AssetType.SEMI_TRUCK, AssetType.FLEET_VEHICLE])
def test_driven_vehicles_get_the_vehicle_inspection(asset_type):
    assert checklist_for(asset_type) == VEHICLE_CHECKLIST


@pytest.mark.parametrize("asset_type", list(AssetType))
def test_no_trailer_is_ever_asked_about_its_engine(asset_type):
    """The whole point of the split: there is nothing under the hood to check."""
    items = checklist_for(asset_type)
    if items == TRAILER_CHECKLIST:
        assert not {"Engine", "Brakes", "Cabin"} & set(items)
