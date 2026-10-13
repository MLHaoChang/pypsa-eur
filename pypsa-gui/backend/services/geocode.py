"""
Address lookup for the map's click-to-place mode (plan 2, M3).

A thin proxy over a Nominatim-compatible endpoint named by
`PYPSAGUI_GEOCODER_URL` (`?format=json&q=…`). The public Nominatim usage
policy forbids heavy automated use, so there is NO default: unset means the
address box is hidden (`configured()` is False) and a search answers 503
with the setting's name. An organisation points it at its own instance or a
commercial Nominatim host.
"""
from __future__ import annotations

import os

import httpx

GEOCODER_URL_ENV = "PYPSAGUI_GEOCODER_URL"
USER_AGENT = "pypsa-gui/1.0.0 (map address placement)"
TIMEOUT_S = 10.0
MAX_RESULTS = 5
MAX_QUERY_CHARS = 200


class GeocoderUnset(RuntimeError):
    """`PYPSAGUI_GEOCODER_URL` is not set."""


class GeocoderUnavailable(RuntimeError):
    """The configured endpoint refused or failed."""


def geocoder_url() -> str | None:
    return os.environ.get(GEOCODER_URL_ENV, "").strip() or None


def configured() -> bool:
    return geocoder_url() is not None


def _parse(rows: object) -> list[dict]:
    """Nominatim rows → `{display_name, lat, lon}`; rows without finite coordinates are dropped."""
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows[:MAX_RESULTS]:
        if not isinstance(row, dict):
            continue
        try:
            lat, lon = float(row.get("lat")), float(row.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        out.append({"display_name": str(row.get("display_name") or ""), "lat": lat, "lon": lon})
    return out


def search(q: str, *, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Forward `q` to the configured geocoder. Raises `GeocoderUnset` / `GeocoderUnavailable`."""
    url = geocoder_url()
    if url is None:
        raise GeocoderUnset(
            f"No geocoder is configured. Set {GEOCODER_URL_ENV} to a Nominatim-compatible endpoint "
            "(for example your own Nominatim instance) to search addresses."
        )
    query = (q or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return []
    try:
        with httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, transport=transport, follow_redirects=True) as client:
            r = client.get(url, params={"format": "json", "q": query, "limit": MAX_RESULTS})
    except httpx.HTTPError as exc:
        raise GeocoderUnavailable(f"The geocoder at {url} could not be reached ({exc.__class__.__name__}).") from exc
    if r.status_code != 200:
        raise GeocoderUnavailable(f"The geocoder at {url} answered HTTP {r.status_code}.")
    try:
        return _parse(r.json())
    except ValueError as exc:
        raise GeocoderUnavailable(f"The geocoder at {url} returned something that is not JSON.") from exc
