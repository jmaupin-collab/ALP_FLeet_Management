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

# Abbreviations are the usual reason a perfectly real address resolves nowhere:
# neither locator matches "2200 Rev Abraham Woods JR BLVD", while the spelled-out
# street matches on the first try.
_STREET_SUFFIXES = {
    "aly": "Alley",
    "ave": "Avenue",
    "blvd": "Boulevard",
    "cir": "Circle",
    "ct": "Court",
    "cv": "Cove",
    "dr": "Drive",
    "expy": "Expressway",
    "fwy": "Freeway",
    "hwy": "Highway",
    "ln": "Lane",
    "pkwy": "Parkway",
    "pl": "Place",
    "plz": "Plaza",
    "rd": "Road",
    "sq": "Square",
    "st": "Street",
    "ter": "Terrace",
    "trl": "Trail",
}

# "St" and "Dr" mean different things depending on where they sit, so titles are
# only expanded in the slot right after the house number: "123 St Charles Ave" is
# Saint Charles Avenue, "123 Main St" is Main Street.
_TITLES = {
    "rev": "Reverend",
    "dr": "Doctor",
    "gen": "General",
    "sgt": "Sergeant",
    "st": "Saint",
    "mt": "Mount",
    "ft": "Fort",
}

_ZIP_TAIL = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")


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
    coords = _geocode_census(text) or _geocode_nominatim(text)
    if coords is not None:
        return coords

    expanded = expand_street_abbreviations(text)
    if expanded == text:
        return None
    logger.info("Both locators came back empty for %r; retrying as %r.", text, expanded)
    return _geocode_census(expanded) or _geocode_nominatim(expanded)


def expand_street_abbreviations(address: str) -> str:
    """Spell out abbreviated titles and street suffixes so a locator can match.

    Only used as a retry, so a wrong guess costs nothing: it either finds the
    address or returns no match exactly as before.
    """
    words = address.split()
    out = []
    for index, word in enumerate(words):
        # Trailing punctuation is carried through so "Blvd," keeps its comma and
        # the city stays a separate part of the line.
        core = word.rstrip(".,")
        trailing = word[len(core) :]
        key = core.lower()
        if index == 1 and key in _TITLES:
            out.append(_TITLES[key] + trailing)
        elif index > 0 and key in _STREET_SUFFIXES:
            out.append(_STREET_SUFFIXES[key] + trailing)
        else:
            out.append(word)
    return " ".join(out)


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
    if coords is None and not has_existing:
        # Last resort, and only when the record would otherwise have no pin at
        # all. A warehouse or agency is a place assets get sent to, so putting it
        # in the right town beats dropping it off the map with nothing to say why.
        # This never runs when precise coordinates already exist.
        coords = geocode_locality(text)
        if coords is not None:
            logger.warning(
                "No street match for %s address %r; pinned to its city/ZIP instead, so this "
                "location is approximate.",
                label,
                text,
            )
    if coords is None:
        logger.warning(
            "Could not geocode %s address %r. The address was saved; coordinates left unchanged.",
            label,
            text,
        )
        return current_lat, current_lng
    return coords[0], coords[1]


def geocode_locality(address: str) -> Optional[Tuple[Decimal, Decimal]]:
    """Resolve just the town an address sits in.

    Public alongside geocode_address because together they are every route out to
    a locator: a caller stubbing one and not the other still reaches the network.

    The ZIP is used when there is one: it is unambiguous nationwide, whereas
    picking the city out of a line like "2200 Rev Abraham Woods JR BLVD
    Birmingham, AL 35203" means guessing where the street stops.
    """
    zip_match = _ZIP_TAIL.search(address)
    if zip_match:
        return _geocode_nominatim(zip_match.group(1), street_level=False)

    # No ZIP: fall back to the trailing "City, ST" if the line ends in one.
    tail = ", ".join(part.strip() for part in address.split(",")[-2:])
    if _CITY_STATE_ZIP.match(tail):
        return _geocode_nominatim(tail, street_level=False)
    return None


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


def _geocode_nominatim(address: str, *, street_level: bool = True) -> Optional[Tuple[Decimal, Decimal]]:
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
        # A ZIP is all digits, so the digit test alone would demand a house
        # number from a query that can never return one.
        wants_street = street_level and any(ch.isdigit() for ch in address)
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
