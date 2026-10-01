"""Street-level geocoding prefers an exact address over a city centroid."""

from decimal import Decimal

from app.geocoding import (
    expand_street_abbreviations,
    geocode_address,
    geocode_directory_address,
    looks_like_street_address,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_geocode_prefers_census_street_match_over_city_center(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        if "census.gov" in url:
            return _FakeResponse(
                {
                    "result": {
                        "addressMatches": [
                            {
                                "matchedAddress": "1317 S FARM RD 131, SPRINGFIELD, MO, 65807",
                                "coordinates": {"x": -93.352527149832, "y": 37.192101492046},
                            }
                        ]
                    }
                }
            )
        return _FakeResponse(
            [{"lat": "37.2081729", "lon": "-93.2922715", "class": "place", "type": "city"}]
        )

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)

    coords = geocode_address("1317 S Farm Rd 131, Springfield, MO 65807")
    assert coords is not None
    assert coords[0] == Decimal("37.192101492046")
    assert coords[1] == Decimal("-93.352527149832")


def test_geocode_skips_city_centroid_when_street_number_is_present(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        if "census.gov" in url:
            return _FakeResponse({"result": {"addressMatches": []}})
        return _FakeResponse(
            [{"lat": "37.2081729", "lon": "-93.2922715", "class": "place", "type": "city"}]
        )

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)
    assert geocode_address("1317 S Farm Rd 131, Springfield, MO 65807") is None


def test_geocode_uses_nominatim_house_result(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        if "census.gov" in url:
            return _FakeResponse({"result": {"addressMatches": []}})
        return _FakeResponse(
            [
                {"lat": "37.2081729", "lon": "-93.2922715", "class": "place", "type": "city"},
                {"lat": "37.1912342", "lon": "-93.3313083", "class": "place", "type": "house"},
            ]
        )

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)
    coords = geocode_address("123 Main St, Springfield, MO")
    assert coords == (Decimal("37.1912342"), Decimal("-93.3313083"))


def test_abbreviations_expand_by_position():
    # A real agency address that resolved nowhere until the abbreviations went.
    assert (
        expand_street_abbreviations("2200 Rev Abraham Woods JR BLVD Birmingham, AL 35203")
        == "2200 Reverend Abraham Woods JR Boulevard Birmingham, AL 35203"
    )
    # "St" is Saint after the house number and Street at the end of the street.
    assert expand_street_abbreviations("123 St Charles Ave") == "123 Saint Charles Avenue"
    assert expand_street_abbreviations("123 Main St, Springfield, MO") == "123 Main Street, Springfield, MO"


def test_geocode_retries_with_the_abbreviations_spelled_out(monkeypatch):
    """The first pass finds nothing; the expanded street matches."""
    seen = []

    def fake_get(self, url, params=None, headers=None, timeout=None):
        query = (params or {}).get("address") or (params or {}).get("q") or ""
        seen.append(query)
        if "Boulevard" not in query:
            return _FakeResponse({"result": {"addressMatches": []}} if "census.gov" in url else [])
        if "census.gov" in url:
            return _FakeResponse({"result": {"addressMatches": []}})
        return _FakeResponse([{"lat": "33.5235208", "lon": "-86.8063399", "class": "highway", "type": "secondary"}])

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)

    coords = geocode_address("2200 Rev Abraham Woods JR BLVD Birmingham, AL 35203")
    assert coords == (Decimal("33.5235208"), Decimal("-86.8063399"))
    assert any("Boulevard" in query for query in seen)


def test_directory_address_falls_back_to_its_zip_rather_than_no_pin(monkeypatch):
    """A warehouse or agency with nowhere on the map is worse than an approximate town."""

    def fake_get(self, url, params=None, headers=None, timeout=None):
        query = (params or {}).get("address") or (params or {}).get("q") or ""
        if "census.gov" in url:
            return _FakeResponse({"result": {"addressMatches": []}})
        if query == "35203":
            return _FakeResponse([{"lat": "33.5170766", "lon": "-86.8080463", "class": "place", "type": "postcode"}])
        return _FakeResponse([])

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)

    coords = geocode_directory_address(
        "2200 Rev Abraham Woods JR BLVD Birmingham, AL 35203", label="Agency"
    )
    assert coords == (Decimal("33.5170766"), Decimal("-86.8080463"))


def test_a_town_centroid_never_replaces_coordinates_we_already_have(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        if "census.gov" in url:
            return _FakeResponse({"result": {"addressMatches": []}})
        return _FakeResponse([{"lat": "33.5170766", "lon": "-86.8080463", "class": "place", "type": "postcode"}])

    monkeypatch.setattr("app.geocoding.requests.Session.get", fake_get)

    existing = (Decimal("33.5234271"), Decimal("-86.8076569"))
    assert (
        geocode_directory_address(
            "2200 Rev Abraham Woods JR BLVD Birmingham, AL 35203", label="Agency", existing=existing
        )
        == existing
    )


def test_looks_like_street_address_rejects_city_state():
    assert looks_like_street_address("1317 S Farm Rd 131, Springfield, MO 65807")
    assert looks_like_street_address("123 Main St, Springfield, MO")
    assert not looks_like_street_address("Springfield, MO 65807")
    assert not looks_like_street_address("Houston, TX")
    assert not looks_like_street_address("Houston PD - Central")
