"""
`/api/geocode` (plan 2, M3): hidden until `PYPSAGUI_GEOCODER_URL` is set,
then a thin proxy over a Nominatim-compatible endpoint.
"""
from __future__ import annotations

import json

import httpx
import pytest

from services import geocode as gc


def test_unset_means_not_configured_and_503(client, monkeypatch):
    monkeypatch.delenv(gc.GEOCODER_URL_ENV, raising=False)
    assert client.get("/api/geocode/status").json() == {"configured": False, "setting": gc.GEOCODER_URL_ENV}
    r = client.get("/api/geocode", params={"q": "Eemshaven"})
    assert r.status_code == 503 and gc.GEOCODER_URL_ENV in r.json()["detail"]


def test_a_stubbed_answer_is_forwarded_with_format_json_and_a_user_agent(monkeypatch):
    monkeypatch.setenv(gc.GEOCODER_URL_ENV, "https://nominatim.example/search")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps([
            {"display_name": "Eemshaven, Groningen", "lat": "53.44", "lon": "6.83", "extra": 1},
            {"display_name": "bad", "lat": "x", "lon": "1"},
        ]))

    rows = gc.search("Eemshaven", transport=httpx.MockTransport(handler))
    assert rows == [{"display_name": "Eemshaven, Groningen", "lat": 53.44, "lon": 6.83}]
    assert seen[0].url.params["format"] == "json" and seen[0].url.params["q"] == "Eemshaven"
    assert seen[0].headers["user-agent"].startswith("pypsa-gui/")


def test_route_answers_through_the_service(client, monkeypatch):
    monkeypatch.setenv(gc.GEOCODER_URL_ENV, "https://nominatim.example/search")
    monkeypatch.setattr(gc, "search", lambda q, **kw: [{"display_name": q, "lat": 1.0, "lon": 2.0}])
    assert client.get("/api/geocode/status").json()["configured"] is True
    assert client.get("/api/geocode", params={"q": "x"}).json() == [{"display_name": "x", "lat": 1.0, "lon": 2.0}]
    assert client.get("/api/geocode", params={"q": ""}).status_code == 422


def test_upstream_failure_is_502(monkeypatch):
    monkeypatch.setenv(gc.GEOCODER_URL_ENV, "https://nominatim.example/search")
    with pytest.raises(gc.GeocoderUnavailable):
        gc.search("x", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(gc.GeocoderUnavailable):
        gc.search("x", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))
