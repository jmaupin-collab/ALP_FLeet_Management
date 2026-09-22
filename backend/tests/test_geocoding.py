"""Street-level geocoding prefers an exact address over a city centroid."""

from decimal import Decimal

from app.geocoding import geocode_address, looks_like_street_address


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


def test_looks_like_street_address_rejects_city_state():
    assert looks_like_street_address("1317 S Farm Rd 131, Springfield, MO 65807")
    assert looks_like_street_address("123 Main St, Springfield, MO")
    assert not looks_like_street_address("Springfield, MO 65807")
    assert not looks_like_street_address("Houston, TX")
    assert not looks_like_street_address("Houston PD - Central")
