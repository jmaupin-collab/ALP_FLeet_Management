"""Geocode entered addresses to street-level coordinates when possible."""

import logging
import re
from decimal import Decimal
from typing import Optional, Tuple

import requests

logger = logging.getLogger("fleet.geocoding")

_CITY_STATE_ZIP = re.compile(
    r"^[A-Za-z][A-Za-z0-9\s\.\-']+,\s*[A-Za-z]{2}(\s+\d{5}(-\d{4})?)?$"
)

_USER_AGENT = "ALPR-Fleet-Management/1.0"


def looks_like_street_address(text: str | None) -> bool:
    """True for a house-number street line; false for city/state or a bare place name."""
    if not text or not text.strip():
        return False
    value = text.strip()
    if _CITY_STATE_ZIP.match(value):
        return False
    return bool(re.match(r"^\d+\s+\S+", value))


def geocode_address(address: str) -> Optional[Tuple[Decimal, Decimal]]:
    """
    Convert an address to GPS coordinates.

    US street addresses use the Census Bureau locator first so the pin is
    on the parcel, not the city center. Nominatim is the fallback.
    """
    if not address or not address.strip():
        return None
    text = address.strip()
    return _geocode_census(text) or _geocode_nominatim(text)


def geocode_directory_address(
    address: str | None,
    *,
    label: str,
    existing: tuple[Decimal | None, Decimal | None] = (None, None),
) -> tuple[Decimal | None, Decimal | None]:
    """Resolve coordinates for a warehouse or agency address.

    Both directory types share this so they geocode identically. Failure is not
    fatal: the entered address is still saved, any existing coordinates survive,
    and a warning is logged. A city/state line never replaces coordinates that
    were already resolved, because that would drag a precise pin to a centroid.
    """
    current_lat, current_lng = existing
    has_existing = current_lat is not None and current_lng is not None
    text = (address or "").strip()
    if not text:
        return current_lat, current_lng

    if has_existing and not looks_like_street_address(text):
        logger.info(
            "%s address %r is not street-level; keeping the more precise existing coordinates.",
            label,
            text,
        )
        return current_lat, current_lng

    coords = geocode_address(text)
    if coords is None:
        logger.warning(
            "Could not geocode %s address %r. The address was saved; coordinates left unchanged.",
            label,
            text,
        )
        return current_lat, current_lng
    return coords[0], coords[1]


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _as_coords(lat, lon) -> Optional[Tuple[Decimal, Decimal]]:
    if lat is None or lon is None:
        return None
    return Decimal(str(lat)), Decimal(str(lon))


def _geocode_census(address: str) -> Optional[Tuple[Decimal, Decimal]]:
    try:
        response = _session().get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={
                "address": address,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=8,
        )
        response.raise_for_status()
        matches = (response.json().get("result") or {}).get("addressMatches") or []
        if not matches:
            return None
        coords = matches[0].get("coordinates") or {}
        return _as_coords(coords.get("y"), coords.get("x"))
    except Exception as exc:
        logger.warning("Census geocoding failed for address %r: %s", address, exc)
        return None


def _geocode_nominatim(address: str) -> Optional[Tuple[Decimal, Decimal]]:
    try:
        response = _session().get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "json",
                "limit": 5,
                "addressdetails": 1,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=8,
        )
        response.raise_for_status()
        rows = response.json() or []
        if not rows:
            return None
        wants_street = any(ch.isdigit() for ch in address)
        ranked = sorted(rows, key=lambda row: _nominatim_rank(row, wants_street))
        best = ranked[0]
        if wants_street and _nominatim_rank(best, True) >= 50:
            return None
        return _as_coords(best.get("lat"), best.get("lon"))
    except Exception as exc:
        logger.warning("Nominatim geocoding failed for address %r: %s", address, exc)
        return None


def _nominatim_rank(row: dict, wants_street: bool) -> int:
    kind = (row.get("type") or "").lower()
    cls = (row.get("class") or "").lower()
    if kind == "house" or cls == "building" or kind == "yes":
        return 0
    if wants_street and kind in {"city", "town", "village", "postcode", "state", "county", "administrative"}:
        return 80
    return 20
