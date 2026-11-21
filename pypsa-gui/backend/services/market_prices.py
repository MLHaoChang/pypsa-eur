"""
Historical day-ahead prices from the ENTSO-E Transparency Platform (SCU06 S1).

The user brings their own ENTSO-E token, stored in `user.env` like a provider key
(`app_secrets.DATA_SERVICE_KEYS`). One fetch is one bidding zone for one complete
UTC calendar year (document A44), parsed into an hourly EUR/MWh series, cached on
disk per zone and year, and landed in the organisation's Library
(`series_store.put_series`), where a business case attaches it by `PriceSeriesRef`
like any other price series.

Token rules (GK B4, SCU06-S1 plan):
* the token travels only as the `securityToken` query parameter to the one fixed
  HTTPS host below, with redirects off, so it can never be carried to another host;
* it never enters the cache file, the series meta or notes, an error message or a
  log line: errors are our own plain words (no status code, no ENTSO-E text), and
  the request line httpx logs has the token blotted out (`_NoSecurityToken`).

Parsing rules: the response is capped at `MAX_RESPONSE_BYTES`; a document with a
DOCTYPE or ENTITY declaration is refused before parsing; every TimeSeries must be
in EUR per MWH; an A01 curve must list every position, while an A03 curve repeats
the last price over skipped positions (its definition). Sub-hourly points (PT15M,
PT30M: the 15-minute day-ahead market since October 2025) are averaged to hourly,
and the series notes say so. A year with any hour missing is refused, never filled.

Live probe (documented, not run in CI; needs a real token), from the backend:

    ENTSOE_API_TOKEN=... python -c "import os; from services import market_prices as m;
    s, n = m.fetch_day_ahead('DE-LU', 2024, token=os.environ['ENTSOE_API_TOKEN']);
    print(len(s), s.mean(), n)"

expects 8784 hourly values for the leap year 2024.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

import httpx
import pandas as pd

import app_paths
from services.atomic_io import atomic_write_text
from services.market_zone_cores import CORES

TOKEN_NAME = "ENTSOE_API_TOKEN"
BASE_URL = "https://web-api.tp.entsoe.eu/api"
USER_AGENT = "pypsa-gui/1.0.0 (day-ahead price fetch)"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
FIRST_YEAR = 2015
SOURCE = "ENTSO-E day-ahead"
PROVIDER = "ENTSO-E Transparency Platform"
CACHE_VERSION = 1
ZONE_FILE = "market_prices.json"


@dataclass(frozen=True)
class Zone:
    code: str
    eic: str
    label: str
    first_year: int = FIRST_YEAR


# Bidding zones with a day-ahead price on the platform. EIC codes from the
# ENTSO-E code list; `first_year` is the first full year the zone existed (DE-LU
# split from DE-AT-LU on 1 Oct 2018; Calabria became its own zone in 2021).
# GB left the platform after Brexit and is not offered.
_ZONES = (
    Zone("AT", "10YAT-APG------L", "Austria", 2019),
    Zone("BE", "10YBE----------2", "Belgium"),
    Zone("BG", "10YCA-BULGARIA-R", "Bulgaria"),
    Zone("CH", "10YCH-SWISSGRIDZ", "Switzerland"),
    Zone("CZ", "10YCZ-CEPS-----N", "Czechia"),
    Zone("DE-LU", "10Y1001A1001A82H", "Germany and Luxembourg", 2019),
    Zone("DK-1", "10YDK-1--------W", "Western Denmark"),
    Zone("DK-2", "10YDK-2--------M", "Eastern Denmark"),
    Zone("EE", "10Y1001A1001A39I", "Estonia"),
    Zone("ES", "10YES-REE------0", "Spain"),
    Zone("FI", "10YFI-1--------U", "Finland"),
    Zone("FR", "10YFR-RTE------C", "France"),
    Zone("GR", "10YGR-HTSO-----Y", "Greece"),
    Zone("HR", "10YHR-HEP------M", "Croatia"),
    Zone("HU", "10YHU-MAVIR----U", "Hungary"),
    Zone("IE-SEM", "10Y1001A1001A59C", "Ireland and Northern Ireland"),
    Zone("IT-CALA", "10Y1001C--00096J", "Italy, Calabria", 2021),
    Zone("IT-CNOR", "10Y1001A1001A70O", "Italy, Centre-North"),
    Zone("IT-CSUD", "10Y1001A1001A71M", "Italy, Centre-South"),
    Zone("IT-NORD", "10Y1001A1001A73I", "Italy, North"),
    Zone("IT-SARD", "10Y1001A1001A74G", "Italy, Sardinia"),
    Zone("IT-SICI", "10Y1001A1001A75E", "Italy, Sicily"),
    Zone("IT-SUD", "10Y1001A1001A788", "Italy, South"),
    Zone("LT", "10YLT-1001A0008Q", "Lithuania"),
    Zone("LV", "10YLV-1001A00074", "Latvia"),
    Zone("NL", "10YNL----------L", "Netherlands"),
    Zone("NO-1", "10YNO-1--------2", "South-Eastern Norway"),
    Zone("NO-2", "10YNO-2--------T", "Southern Norway"),
    Zone("NO-3", "10YNO-3--------J", "Central Norway"),
    Zone("NO-4", "10YNO-4--------9", "Northern Norway"),
    Zone("NO-5", "10Y1001A1001A48H", "Western Norway"),
    Zone("PL", "10YPL-AREA-----S", "Poland"),
    Zone("PT", "10YPT-REN------W", "Portugal"),
    Zone("RO", "10YRO-TEL------P", "Romania"),
    Zone("RS", "10YCS-SERBIATSOV", "Serbia"),
    Zone("SE-1", "10Y1001A1001A44P", "Northern Sweden"),
    Zone("SE-2", "10Y1001A1001A45N", "North-Central Sweden"),
    Zone("SE-3", "10Y1001A1001A46L", "South-Central Sweden"),
    Zone("SE-4", "10Y1001A1001A47J", "Southern Sweden"),
    Zone("SI", "10YSI-ELES-----O", "Slovenia"),
    Zone("SK", "10YSK-SEPS-----K", "Slovakia"),
)
BIDDING_ZONES: dict[str, Zone] = {z.code: z for z in _ZONES}


_TOKEN_IN_URL = re.compile(r"(securityToken=)[^&\s\"']+", re.IGNORECASE)


class _NoSecurityToken(logging.Filter):
    """Blot the token out of a record that carries the request URL: httpx logs
    every request line, query string included, at INFO."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — an unformattable record is dropped
            return False
        if "securitytoken" in message.lower():
            record.msg, record.args = _TOKEN_IN_URL.sub(r"\1<redacted>", message), ()
        return True


# The filter sits on the loggers that print request URLs (the deepgram_stt
# precedent), so no log level or handler reaches the token.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_NoSecurityToken())


class MarketPriceError(Exception):
    """A refusal said in plain words. `kind` is stable for the UI and tests;
    the message never carries the token, a URL or ENTSO-E's own text."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# ── zone and year ──────────────────────────────────────────────────────────


def zone_for_pin(lat: float, lng: float) -> str | None:
    """The bidding zone a site pin lies in, or None when the bundled cores
    cannot tell (near a border or coast, or in a multi-zone country): the
    lookup never guesses (`services/market_zone_cores.py`)."""
    if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    hits = [
        zone
        for zone, rects in CORES.items()
        if any(w <= lng < e and s <= lat < n for w, s, e, n in rects)
    ]
    return hits[0] if len(hits) == 1 else None


def pin_of(sites_doc: dict) -> tuple[float, float] | None:
    """The first site's pin `(lat, lng)` from a `sites.json` document, or None."""
    for site in sites_doc.get("sites") or []:
        origin = site.get("origin") if isinstance(site, dict) else None
        if isinstance(origin, dict):
            lat, lng = origin.get("lat"), origin.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                return float(lat), float(lng)
    return None


def last_complete_year(now: datetime | None = None) -> int:
    return (now or datetime.now(UTC)).year - 1


def check_request(zone: str, year: int, now: datetime | None = None) -> Zone:
    """Validate the zone and year before either touches a path or a request."""
    z = BIDDING_ZONES.get(zone) if isinstance(zone, str) else None
    if z is None:
        raise MarketPriceError(
            "invalid_zone", "Bidding zone: choose a zone from the list."
        )
    last = last_complete_year(now)
    if (
        isinstance(year, bool)
        or not isinstance(year, int)
        or not (z.first_year <= year <= last)
    ):
        raise MarketPriceError(
            "invalid_year",
            f"Year: choose a complete year from {z.first_year} to {last}.",
        )
    return z


# ── project zone choice ────────────────────────────────────────────────────


def chosen_zone(project_dir: Path) -> str | None:
    """The zone the user picked for this project, or None (missing, unreadable
    or no longer a zone: the pin then decides)."""
    try:
        data = json.loads((project_dir / ZONE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    zone = data.get("zone") if isinstance(data, dict) else None
    return zone if zone in BIDDING_ZONES else None


def store_zone(project_dir: Path, zone: str) -> None:
    if zone not in BIDDING_ZONES:
        raise MarketPriceError(
            "invalid_zone", "Bidding zone: choose a zone from the list."
        )
    atomic_write_text(project_dir / ZONE_FILE, json.dumps({"zone": zone}))


# ── fetch and parse ────────────────────────────────────────────────────────


def token() -> str | None:
    value = os.environ.get(TOKEN_NAME, "").strip()
    return value or None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str) -> str | None:
    c = _child(el, name) if el is not None else None
    return c.text.strip() if c is not None and c.text else None


_RESOLUTION = {"PT15M": 15, "PT30M": 30, "PT60M": 60, "PT1H": 60}
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _bad(message: str) -> MarketPriceError:
    return MarketPriceError("bad_document", message)


def _ts(text: str | None) -> pd.Timestamp:
    try:
        t = pd.Timestamp(text)
    except (TypeError, ValueError) as exc:
        raise _bad("ENTSO-E sent a period with an unreadable time.") from exc
    if t.tzinfo is None:
        raise _bad("ENTSO-E sent a period with an unreadable time.")
    return t.tz_convert("UTC")


def parse_a44(xml: bytes, zone: str, year: int) -> tuple[pd.Series, list[str]]:
    """An A44 Publication_MarketDocument → (hourly UTC EUR/MWh series for `year`,
    extra notes). Raises `MarketPriceError` with a plain message."""
    if len(xml) > MAX_RESPONSE_BYTES:
        raise _bad("ENTSO-E sent more data than one year of prices can be.")
    if _FORBIDDEN.search(xml):
        raise _bad("ENTSO-E sent a document this app does not read.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise _bad("ENTSO-E sent a document that could not be read.") from exc
    name = _local(root.tag)
    if name == "Acknowledgement_MarketDocument":
        raise MarketPriceError(
            "no_data", f"ENTSO-E has no day-ahead prices for {zone} in {year}."
        )
    if name != "Publication_MarketDocument":
        raise _bad("ENTSO-E sent a document that is not a price list.")

    # minutes of resolution → {interval start: price}
    points: dict[int, dict[pd.Timestamp, float]] = {}
    for series in (c for c in root if _local(c.tag) == "TimeSeries"):
        if _text(series, "currency_Unit.name") != "EUR":
            raise _bad("ENTSO-E sent prices in a currency other than EUR.")
        if (_text(series, "price_Measure_Unit.name") or "").upper() != "MWH":
            raise _bad("ENTSO-E sent prices per a unit other than MWh.")
        curve = _text(series, "curveType") or "A01"
        if curve not in ("A01", "A03"):
            raise _bad("ENTSO-E sent a price curve this app does not read.")
        for period in (c for c in series if _local(c.tag) == "Period"):
            interval = _child(period, "timeInterval")
            start, end = _ts(_text(interval, "start")), _ts(_text(interval, "end"))
            minutes = _RESOLUTION.get(_text(period, "resolution") or "")
            if minutes is None:
                raise _bad(
                    "ENTSO-E sent prices at a resolution this app does not read."
                )
            count = int((end - start) / pd.Timedelta(minutes=minutes))
            if count <= 0:
                raise _bad("ENTSO-E sent an empty price period.")
            given: dict[int, float] = {}
            for pt in (c for c in period if _local(c.tag) == "Point"):
                try:
                    pos = int(_text(pt, "position") or "")
                    price = float(_text(pt, "price.amount") or "")
                except ValueError as exc:
                    raise _bad("ENTSO-E sent a price that is not a number.") from exc
                if not (1 <= pos <= count) or not math.isfinite(price):
                    raise _bad("ENTSO-E sent a price outside its period.")
                given[pos] = price
            if curve == "A01" and len(given) != count:
                raise _bad(f"ENTSO-E left {count - len(given)} prices out of a period.")
            if 1 not in given:
                raise _bad("ENTSO-E sent a period without its first price.")
            bucket = points.setdefault(minutes, {})
            last = given[1]
            for pos in range(1, count + 1):
                last = given.get(
                    pos, last
                )  # A03: a skipped position repeats the last price
                bucket[start + pd.Timedelta(minutes=minutes * (pos - 1))] = last

    if not points:
        raise MarketPriceError(
            "no_data", f"ENTSO-E has no day-ahead prices for {zone} in {year}."
        )
    lo = pd.Timestamp(f"{year}-01-01", tz="UTC")
    hours = pd.date_range(
        lo, pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), freq="h", inclusive="left"
    )
    notes: list[str] = []
    hourly = pd.Series(float("nan"), index=hours)
    # Coarse first, finer on top: where the 15-minute market published, its
    # hourly mean is the hour's price.
    for minutes in sorted(points, reverse=True):
        s = pd.Series(points[minutes]).sort_index()
        if minutes < 60:
            per_hour = s.groupby(s.index.floor("h")).agg(["mean", "count"])
            s = per_hour["mean"][per_hour["count"] == 60 // minutes]
            notes.append(f"resolution:PT{minutes}M averaged to hourly")
        s = s[(s.index >= lo) & (s.index < hours[-1] + pd.Timedelta(hours=1))]
        hourly.loc[s.index] = s.values
    missing = int(hourly.isna().sum())
    if missing:
        raise MarketPriceError(
            "incomplete",
            f"ENTSO-E is missing {missing} hours of {zone} prices in {year}.",
        )
    return hourly.rename(None), notes


def _period(year: int) -> dict[str, str]:
    return {"periodStart": f"{year}01010000", "periodEnd": f"{year + 1}01010000"}


def fetch_day_ahead(
    zone: str, year: int, *, token: str, transport: httpx.BaseTransport | None = None
) -> tuple[pd.Series, list[str]]:
    """One request to ENTSO-E for `zone`'s day-ahead prices in `year` (UTC)."""
    z = check_request(zone, year)
    params = {
        "securityToken": token,
        "documentType": "A44",
        "in_Domain": z.eic,
        "out_Domain": z.eic,
        **_period(year),
    }
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", BASE_URL, params=params) as r:
                status = r.status_code
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise _bad(
                            "ENTSO-E sent more data than one year of prices can be."
                        )
    except httpx.TimeoutException as exc:
        raise MarketPriceError(
            "unavailable", "ENTSO-E did not answer in time. Try again later."
        ) from exc
    except httpx.HTTPError as exc:
        raise MarketPriceError(
            "unavailable",
            "ENTSO-E could not be reached. Check the connection and try again.",
        ) from exc
    if status == 401 or status == 403:
        raise MarketPriceError(
            "bad_token",
            "ENTSO-E did not accept the access token. Check it "
            "on the Transparency Platform and save it again.",
        )
    if status == 429:
        raise MarketPriceError(
            "unavailable", "ENTSO-E is limiting requests. Try again in a minute."
        )
    if status == 400:
        # An Acknowledgement (no data for the query) answers 400 as well as 200.
        return parse_a44(bytes(body), zone, year)
    if status != 200:
        raise MarketPriceError(
            "unavailable", "ENTSO-E could not answer just now. Try again later."
        )
    return parse_a44(bytes(body), zone, year)


# ── cache ──────────────────────────────────────────────────────────────────


def cache_dir() -> Path:
    return app_paths.app_data_dir() / "market_prices" / "entsoe"


def _cache_file(zone: str, year: int) -> Path:
    # Only ever called after `check_request`: zone is a key of BIDDING_ZONES
    # and year an int, so the path cannot leave the cache directory.
    return cache_dir() / zone / f"{year}.json"


def _read_cache(zone: str, year: int) -> tuple[pd.Series, list[str], str] | None:
    """A complete cached year, or None (absent, partial or corrupt: refetch)."""
    try:
        data = json.loads(_cache_file(zone, year).read_text(encoding="utf-8"))
        if (
            data.get("version") != CACHE_VERSION
            or data.get("zone") != zone
            or data.get("year") != year
        ):
            return None
        values = [float(v) for v in data["values"]]
        hours = pd.date_range(
            f"{year}-01-01", f"{year + 1}-01-01", freq="h", tz="UTC", inclusive="left"
        )
        if len(values) != len(hours) or not all(math.isfinite(v) for v in values):
            return None
        notes = [str(n) for n in data.get("notes", [])]
        fetched = str(data["fetched"])
        datetime.strptime(fetched, "%Y-%m-%d")
        return pd.Series(values, index=hours), notes, fetched
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_cache(
    zone: str, year: int, series: pd.Series, notes: list[str], fetched: str
) -> None:
    path = _cache_file(zone, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {
                "version": CACHE_VERSION,
                "zone": zone,
                "year": year,
                "fetched": fetched,
                "notes": notes,
                "values": [float(v) for v in series.values],
            }
        ),
    )


def day_ahead(
    zone: str,
    year: int,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> tuple[pd.Series, list[str], str]:
    """`(series, notes, fetched ISO date)` for a complete year: from the cache
    when it holds the year (a complete year never changes), else one fetch
    with the stored token, then cached."""
    check_request(zone, year, now)
    cached = _read_cache(zone, year)
    if cached is not None:
        return cached
    tok = token()
    if tok is None:
        raise MarketPriceError(
            "no_token", "Day-ahead prices need an ENTSO-E access token."
        )
    series, notes = fetch_day_ahead(zone, year, token=tok, transport=transport)
    fetched = (now or datetime.now(UTC)).date().isoformat()
    _write_cache(zone, year, series, notes, fetched)
    return series, notes, fetched


# ── landing ────────────────────────────────────────────────────────────────


def series_name(zone: str, year: int) -> str:
    return f"entsoe_da_{zone}_{year}"


def series_meta(zone: str, year: int, notes: list[str], fetched: str) -> dict:
    """Library meta: source, fetch date, zone and year; money notes in the
    template-series convention (`services/library/template_series.py`)."""
    return {
        "source": SOURCE,
        "provider": PROVIDER,
        "vintage_year": year,
        "unit": "EUR/MWh",
        "label": f"{zone} day-ahead {year}",
        "description": f"{SOURCE} prices for {BIDDING_ZONES[zone].label}, {year}, "
        f"fetched {fetched}.",
        "notes": [
            "currency:EUR",
            "price_basis:nominal",
            f"currency_year:{year}",
            f"region:{zone}",
            "source_time_basis:UTC",
            f"fetched:{fetched}",
            "document:A44",
            *notes,
        ],
    }


def land(
    db,
    org_id,
    zone: str,
    year: int,
    *,
    created_by=None,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
    root: Path | None = None,
):
    """Fetch (or read the cache) and store the year in the org's Library;
    returns `(PriceSeriesRef, fetched ISO date)`."""
    from services.library import series_store

    series, notes, fetched = day_ahead(zone, year, transport=transport, now=now)
    ref = series_store.put_series(
        db,
        org_id,
        series_name(zone, year),
        series,
        series_meta(zone, year, notes, fetched),
        created_by=created_by,
        root=root,
    )
    return ref, fetched
