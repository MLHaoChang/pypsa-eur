"""
ENTSO-E day-ahead prices (SCU06 S1, `services/market_prices.py`).

Every response is replayed through `httpx.MockTransport`: CI never reaches the
network. The main fixture (`tests/fixtures/entsoe/de_lu_2024_a44.xml.gz`) is the
A44 layout built from the shipped SMARD DE-LU 2024 seed (its `build.py` says
how); the edge cases are built here with the same writer.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
from datetime import datetime, UTC
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from services import app_secrets
from services import market_prices as M
from tests.fixtures.entsoe import build as B

FIXTURE = Path(__file__).parent / "fixtures" / "entsoe" / "de_lu_2024_a44.xml.gz"
TOKEN = "entsoe-test-token-3f9c2a71-0b5d-4e8e-9d1a-7c6b5a4f3e2d"
NOW = datetime(2026, 11, 21, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    previous, shell = os.environ.get(M.TOKEN_NAME), app_secrets._SHELL_NAMES
    os.environ.pop(M.TOKEN_NAME, None)
    app_secrets._SHELL_NAMES = frozenset()
    yield
    app_secrets._SHELL_NAMES = shell
    if previous is None:
        os.environ.pop(M.TOKEN_NAME, None)
    else:
        os.environ[M.TOKEN_NAME] = previous


def _fixture() -> bytes:
    return gzip.decompress(FIXTURE.read_bytes())


class Replay:
    """A MockTransport that answers every request with one response and
    records the requests it saw."""

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: dict | None = None,
        exc: Exception | None = None,
    ):
        self.requests: list[httpx.Request] = []
        self.status, self.body, self.headers, self.exc = (
            status,
            body,
            headers or {},
            exc,
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, content=self.body, headers=self.headers)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _hours(year: int) -> pd.DatetimeIndex:
    return pd.date_range(
        f"{year}-01-01", f"{year + 1}-01-01", freq="h", tz="UTC", inclusive="left"
    )


def _doc(prices: pd.Series, **kw) -> bytes:
    return B.a44(prices, **kw).encode()


def _year_prices(
    year: int, *, start_offset_h: int = -1, end_extra_h: int = 23
) -> pd.Series:
    """Prices for whole CET days covering the UTC year: value = hour index mod 50."""
    idx = pd.date_range(
        pd.Timestamp(f"{year}-01-01", tz="UTC") + pd.Timedelta(hours=start_offset_h),
        pd.Timestamp(f"{year + 1}-01-01", tz="UTC") + pd.Timedelta(hours=end_extra_h),
        freq="h",
        inclusive="left",
    )
    return pd.Series((np.arange(len(idx)) % 50).astype(float), index=idx)


# ── parsing ────────────────────────────────────────────────────────────────


def test_the_fixture_parses_to_the_seed_year_hour_by_hour():
    series, notes = M.parse_a44(_fixture(), "DE-LU", 2024)
    seed = B.seed()
    assert len(series) == 8784  # leap year
    assert series.index.equals(_hours(2024))
    assert np.allclose(series.to_numpy(), seed.to_numpy())
    assert notes == []  # hourly: nothing averaged


def test_the_dst_days_carry_23_and_25_points_and_still_fill_every_utc_hour():
    raw = _fixture().decode()
    assert (
        "<start>2024-03-30T23:00Z</start>\n        <end>2024-03-31T22:00Z</end>" in raw
    )
    assert (
        "<start>2024-10-26T22:00Z</start>\n        <end>2024-10-27T23:00Z</end>" in raw
    )
    series, _ = M.parse_a44(raw.encode(), "DE-LU", 2024)
    assert series.isna().sum() == 0


def test_a03_repeats_the_last_price_over_skipped_positions():
    p = _year_prices(2023)
    p[:] = 7.0
    p.iloc[31:41] = -3.5  # p starts one hour before the year
    series, _ = M.parse_a44(_doc(p, curve="A03"), "DE-LU", 2023)
    assert len(series) == 8760
    assert (
        series.iloc[29] == 7.0
        and (series.iloc[30:40] == -3.5).all()
        and series.iloc[40] == 7.0
    )


def test_an_a01_curve_with_a_missing_position_is_refused_not_zero_filled():
    raw = _doc(_year_prices(2023), curve="A01").decode()
    cut = raw.index("<position>5</position>")
    start, end = (
        raw.rindex("<Point>", 0, cut),
        raw.index("</Point>", cut) + len("</Point>"),
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44((raw[:start] + raw[end:]).encode(), "DE-LU", 2023)
    assert e.value.kind == "bad_document" and "left 1 prices out" in e.value.message


def test_quarter_hours_are_averaged_to_hourly_and_noted():
    hourly = _year_prices(2025)
    q = pd.Series(
        np.repeat(hourly.to_numpy(), 4) + np.tile([-1.5, -0.5, 0.5, 1.5], len(hourly)),
        index=pd.date_range(hourly.index[0], periods=4 * len(hourly), freq="15min"),
    )
    # Hourly until October, quarter-hours after: the 2025 market change.
    split = pd.Timestamp("2025-09-30T22:00Z")
    doc_h = B.a44(hourly[hourly.index < split], curve="A01")
    doc_q = B.a44(q[q.index >= split], resolution="PT15M", curve="A01")
    body = (
        doc_h.split("</Publication_MarketDocument>")[0]
        + doc_q.split("</period.timeInterval>", 1)[1]
    )
    series, notes = M.parse_a44(body.encode(), "DE-LU", 2025)
    expected = hourly[
        (hourly.index >= _hours(2025)[0]) & (hourly.index <= _hours(2025)[-1])
    ]
    assert np.allclose(series.to_numpy(), expected.to_numpy())
    assert notes == ["resolution:PT15M averaged to hourly"]


def test_a_missing_hour_refuses_the_year():
    p = _year_prices(2023).drop(pd.Timestamp("2023-06-01T12:00Z"))
    # A01 per day, now with a day one hour short: drop that day's TimeSeries instead.
    raw = _doc(_year_prices(2023)).decode()
    day = raw.index("<start>2023-05-31T22:00Z</start>")
    ts_start = raw.rindex("<TimeSeries>", 0, day)
    ts_end = raw.index("</TimeSeries>", day) + len("</TimeSeries>")
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44((raw[:ts_start] + raw[ts_end:]).encode(), "DE-LU", 2023)
    assert e.value.kind == "incomplete" and "missing 24 hours" in e.value.message
    assert len(p) == len(_year_prices(2023)) - 1


@pytest.mark.parametrize(
    "decl",
    [
        b'<!DOCTYPE x [<!ENTITY a "b">]>',
        b'<!ENTITY a SYSTEM "file:///etc/passwd">',
        b"<!doctype html>",
    ],
)
def test_doctype_and_entity_declarations_are_refused_before_parsing(decl):
    raw = _doc(_year_prices(2023))
    body = raw.replace(
        b"<Publication_MarketDocument", decl + b"<Publication_MarketDocument", 1
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body, "DE-LU", 2023)
    assert e.value.kind == "bad_document"


@pytest.mark.parametrize(
    "swap, words",
    [
        (
            (b"<currency_Unit.name>EUR<", b"<currency_Unit.name>USD<"),
            "currency other than EUR",
        ),
        (
            (b"<price_Measure_Unit.name>MWH<", b"<price_Measure_Unit.name>KWH<"),
            "unit other than MWh",
        ),
        ((b"<resolution>PT60M<", b"<resolution>P1D<"), "resolution"),
        ((b"<curveType>A03<", b"<curveType>A02<"), "price curve"),
        ((b"<price.amount>1<", b"<price.amount>abc<"), "not a number"),
        ((b"<position>2<", b"<position>99<"), "outside its period"),
    ],
)
def test_a_document_off_the_rules_is_refused_in_words(swap, words):
    body = _doc(_year_prices(2023)).replace(*swap, 1)
    assert body != _doc(_year_prices(2023))
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body, "DE-LU", 2023)
    assert e.value.kind == "bad_document" and words in e.value.message


def _utf16(body: bytes, codec: str) -> bytes:
    text = re.sub(
        r'encoding="utf-8"', f'encoding="{codec}"', body.decode("utf-8"), flags=re.I
    )
    return text.encode(codec)


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-16", "utf-32"])
def test_a_utf16_or_utf32_body_is_refused_even_with_a_hidden_entity(codec):
    """GK B4 (2) on #1850: a UTF-16 body hid its DOCTYPE from a byte search
    while expat decoded and expanded it. `utf-16` adds a BOM; -le/-be have none."""
    raw = _doc(_year_prices(2023))
    body = raw.replace(
        b"<Publication_MarketDocument",
        b'<!DOCTYPE x [<!ENTITY a "9">]><Publication_MarketDocument',
        1,
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(_utf16(body, codec), "DE-LU", 2023)
    assert e.value.kind == "bad_document"
    # A clean year re-encoded is refused as well: only UTF-8 is read.
    with pytest.raises(M.MarketPriceError):
        M.parse_a44(_utf16(raw, codec), "DE-LU", 2023)


def test_a_utf8_bom_and_a_declared_utf8_still_parse():
    raw = _doc(_year_prices(2023))
    assert len(M.parse_a44(b"\xef\xbb\xbf" + raw, "DE-LU", 2023)[0]) == 8760


def test_a_declaration_naming_another_encoding_is_refused():
    raw = _doc(_year_prices(2023))
    body = re.sub(rb'encoding="utf-8"', b'encoding="ISO-8859-1"', raw, flags=re.I)
    assert body != raw
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body, "DE-LU", 2023)
    assert e.value.kind == "bad_document"


def test_an_oversized_document_is_refused():
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(b" " * (M.MAX_RESPONSE_BYTES + 1), "DE-LU", 2023)
    assert e.value.kind == "bad_document"


ACK = (
    b'<?xml version="1.0" encoding="UTF-8"?><Acknowledgement_MarketDocument '
    b'xmlns="urn:iec62325.351:tc57wg16:451-1:acknowledgementdocument:7:0"><mRID>x</mRID>'
    b"<Reason><code>999</code><text>No matching data found for Data item Day-ahead Prices "
    b"[12.1.D] (10Y1001A1001A82H, 10Y1001A1001A82H) and interval 2016-01-01T00:00:00.000Z/"
    b"2017-01-01T00:00:00.000Z.</text></Reason></Acknowledgement_MarketDocument>"
)


def test_an_acknowledgement_is_no_data_in_our_words_not_entsoes():
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(ACK, "DE-LU", 2023)
    assert e.value.kind == "no_data"
    assert e.value.message == "ENTSO-E has no day-ahead prices for DE-LU in 2023."


# ── the request ────────────────────────────────────────────────────────────


def test_the_request_names_the_zone_eic_and_the_utc_year():
    r = Replay(body=_fixture())
    series, _ = M.fetch_day_ahead("DE-LU", 2024, token=TOKEN, transport=r.transport)
    assert len(series) == 8784
    (req,) = r.requests
    assert req.url.scheme == "https" and req.url.host == "web-api.tp.entsoe.eu"
    q = dict(req.url.params)
    assert q == {
        "securityToken": TOKEN,
        "documentType": "A44",
        "in_Domain": "10Y1001A1001A82H",
        "out_Domain": "10Y1001A1001A82H",
        "periodStart": "202401010000",
        "periodEnd": "202501010000",
    }
    assert req.headers["user-agent"] == M.USER_AGENT


@pytest.mark.parametrize(
    "status, kind",
    [
        (401, "bad_token"),
        (403, "bad_token"),
        (429, "unavailable"),
        (500, "unavailable"),
        (503, "unavailable"),
        (302, "unavailable"),
    ],
)
def test_http_refusals_are_plain_and_carry_no_token(status, kind):
    r = Replay(
        status=status,
        body=b"<html>Unauthorized " + TOKEN.encode() + b"</html>",
        headers={"location": "https://elsewhere.example/steal"},
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead("DE-LU", 2024, token=TOKEN, transport=r.transport)
    assert e.value.kind == kind
    assert TOKEN not in str(e.value) and "http" not in e.value.message.lower()
    assert str(status) not in e.value.message  # no raw status code in the words
    assert len(r.requests) == 1  # a redirect is never followed


def test_a_400_acknowledgement_is_no_data():
    r = Replay(status=400, body=ACK)
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead("DE-LU", 2024, token=TOKEN, transport=r.transport)
    assert e.value.kind == "no_data"


@pytest.mark.parametrize(
    "exc, words",
    [
        (httpx.ConnectTimeout("t"), "did not answer in time"),
        (httpx.ConnectError("c"), "could not be reached"),
    ],
)
def test_network_failures_are_plain(exc, words):
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead(
            "DE-LU", 2024, token=TOKEN, transport=Replay(exc=exc).transport
        )
    assert e.value.kind == "unavailable" and words in e.value.message


def test_a_stream_past_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(M, "MAX_RESPONSE_BYTES", 1000)
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead(
            "DE-LU", 2024, token=TOKEN, transport=Replay(body=b"x" * 5000).transport
        )
    assert e.value.kind == "bad_document"


def test_reading_stops_at_the_first_chunk_over_the_cap(monkeypatch):
    """GK B4 (2): the in-stream cap stops the read; without it parse_a44 would
    still refuse, but only after the whole body was read into memory."""
    monkeypatch.setattr(M, "MAX_RESPONSE_BYTES", 1000)
    pulled = []

    class Endless(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                pulled.append(i)
                yield b"x" * 600

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Endless())
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead("DE-LU", 2024, token=TOKEN, transport=transport)
    assert e.value.kind == "bad_document" and len(pulled) == 2


# ── zone and year ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("zone", ["XX", "../../etc", "de-lu", "", None, 5])
def test_an_unknown_zone_is_refused_before_any_path_or_request(zone):
    with pytest.raises(M.MarketPriceError) as e:
        M.check_request(zone, 2024, NOW)
    assert e.value.kind == "invalid_zone" and e.value.message.startswith(
        "Bidding zone:"
    )


@pytest.mark.parametrize(
    "zone, year",
    [
        ("DE-LU", 2018),
        ("DE-LU", 2026),
        ("FR", 2014),
        ("FR", True),
        ("FR", "2024"),
        ("IT-CALA", 2020),
    ],
)
def test_a_year_outside_the_zone_s_complete_years_is_refused(zone, year):
    with pytest.raises(M.MarketPriceError) as e:
        M.check_request(zone, year, NOW)
    assert e.value.kind == "invalid_year" and e.value.message.startswith("Year:")


def test_the_last_complete_year_is_accepted_and_the_current_one_refused():
    assert M.check_request("FR", 2025, NOW).code == "FR"
    assert M.check_request("FR", 2015, NOW).code == "FR"
    assert M.check_request("DE-LU", 2019, NOW).code == "DE-LU"


def _eic_check(code: str) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-"
    total = sum(alphabet.index(ch) * (16 - i) for i, ch in enumerate(code[:15]))
    return alphabet[36 - ((total - 1) % 37)]


def test_every_eic_code_passes_its_check_character():
    for zone in M.BIDDING_ZONES.values():
        assert len(zone.eic) == 16 and _eic_check(zone.eic) == zone.eic[15], zone.code


@pytest.mark.parametrize(
    "lat, lng, zone",
    [
        (52.52, 13.40, "DE-LU"),  # Berlin
        (48.14, 11.58, "DE-LU"),  # Munich
        (48.86, 2.35, "FR"),  # Paris
        (40.42, -3.70, "ES"),  # Madrid
        (52.23, 21.01, "PL"),  # Warsaw
        (48.21, 16.37, "AT"),  # Vienna
        (47.56, 7.59, None),  # Basel: three borders
        (50.78, 6.08, None),  # Aachen: near NL and BE
        (59.91, 10.75, None),  # Oslo: Norway has five zones
        (45.46, 9.19, None),  # Milan: Italy has seven
        (55.68, 12.57, None),  # Copenhagen: two zones
        (51.51, -0.13, None),  # London: not on the platform
        (float("nan"), 13.4, None),
    ],
)
def test_the_pin_lookup_names_a_zone_only_when_it_is_sure(lat, lng, zone):
    assert M.zone_for_pin(lat, lng) == zone


def test_every_core_lies_in_a_known_zone():
    assert set(M.CORES) <= set(M.BIDDING_ZONES)
    assert not {"DK-1", "DK-2", "NO-2", "SE-3", "IT-NORD"} & set(M.CORES)


def test_pin_of_reads_the_first_site_with_a_pin():
    doc = {"sites": [{"id": "a"}, {"id": "b", "origin": {"lat": 52.5, "lng": 13.4}}]}
    assert M.pin_of(doc) == (52.5, 13.4)
    assert M.pin_of({"sites": []}) is None
    assert M.pin_of({"sites": [{"origin": {"lat": "x", "lng": 1}}]}) is None


def test_the_chosen_zone_round_trips_and_a_stale_one_reads_as_none(tmp_path):
    assert M.chosen_zone(tmp_path) is None
    M.store_zone(tmp_path, "NO-2")
    assert M.chosen_zone(tmp_path) == "NO-2"
    (tmp_path / M.ZONE_FILE).write_text('{"zone": "../x"}')
    assert M.chosen_zone(tmp_path) is None
    (tmp_path / M.ZONE_FILE).write_text("{not json")
    assert M.chosen_zone(tmp_path) is None
    with pytest.raises(M.MarketPriceError):
        M.store_zone(tmp_path, "XX")


# ── cache and token ────────────────────────────────────────────────────────


def test_no_token_is_a_plain_refusal_and_no_request():
    r = Replay(body=_fixture())
    with pytest.raises(M.MarketPriceError) as e:
        M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    assert e.value.kind == "no_token"
    assert e.value.message == "Day-ahead prices need an ENTSO-E access token."
    assert r.requests == []


def test_a_fetched_year_is_cached_and_never_fetched_twice():
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    r = Replay(body=_fixture())
    s1, notes1, fetched1 = M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    s2, notes2, fetched2 = M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    assert len(r.requests) == 1
    assert s1.equals(s2) and notes1 == notes2 and fetched1 == fetched2 == "2026-11-21"
    cache = M.cache_dir() / "DE-LU" / "2024.json"
    assert TOKEN not in cache.read_text()


@pytest.mark.parametrize(
    "damage",
    [
        lambda p: p.write_text("{not json"),
        lambda p: p.write_text(
            json.dumps({**json.loads(p.read_text()), "values": [1.0] * 10})
        ),
        lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "version": 0})),
        lambda p: p.write_text(
            json.dumps({**json.loads(p.read_text()), "fetched": "yesterday"})
        ),
        lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "zone": "FR"})),
    ],
)
def test_a_corrupt_or_partial_cache_refetches(damage):
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    r = Replay(body=_fixture())
    M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    damage(M.cache_dir() / "DE-LU" / "2024.json")
    series, _, _ = M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    assert len(r.requests) == 2 and len(series) == 8784


def test_the_token_never_reaches_a_log_line(caplog):
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    caplog.set_level(logging.DEBUG)
    M.day_ahead("DE-LU", 2024, transport=Replay(body=_fixture()).transport, now=NOW)
    with pytest.raises(M.MarketPriceError):
        M.fetch_day_ahead(
            "FR", 2024, token=TOKEN, transport=Replay(status=401).transport
        )
    assert TOKEN not in caplog.text
    assert "securityToken=<redacted>" in caplog.text  # the request line is still logged


def test_the_token_is_a_managed_secret_and_redacted():
    assert app_secrets.is_managed_key(M.TOKEN_NAME)
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    assert TOKEN in app_secrets.live_secret_values()
    app_secrets.clear_secret(M.TOKEN_NAME)
    assert M.token() is None


def test_the_landing_meta_names_source_date_zone_and_year_and_no_token():
    meta = M.series_meta(
        "DE-LU", 2024, ["resolution:PT15M averaged to hourly"], "2026-11-21"
    )
    assert meta["source"] == "ENTSO-E day-ahead" and meta["vintage_year"] == 2024
    assert meta["label"] == "DE-LU day-ahead 2024" and meta["unit"] == "EUR/MWh"
    assert {
        "currency:EUR",
        "price_basis:nominal",
        "currency_year:2024",
        "region:DE-LU",
        "fetched:2026-11-21",
        "resolution:PT15M averaged to hourly",
    } <= set(meta["notes"])
    assert TOKEN not in json.dumps(meta)


# ── landing ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(_auth_db):
    _engine, session_local = _auth_db
    with session_local() as s:
        yield s


def test_land_stores_the_year_in_the_org_library(db, seeded_identity, tmp_path):
    from services.library import series_store as S

    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    org = seeded_identity["org_id"]
    ref, fetched = M.land(
        db,
        org,
        "DE-LU",
        2024,
        transport=Replay(body=_fixture()).transport,
        now=NOW,
        root=tmp_path,
    )
    assert fetched == "2026-11-21"
    assert ref.id == "entsoe_da_DE-LU_2024" and ref.source == "ENTSO-E day-ahead"
    series = S.resolve(db, org, ref, root=tmp_path)
    assert len(series) == 8784
    from db.models import LibraryItem

    (row,) = db.query(LibraryItem).filter_by(org_id=org, name=ref.id).all()
    assert TOKEN not in row.meta_json and "fetched:2026-11-21" in row.meta_json
    assert TOKEN not in json.dumps(ref.model_dump(mode="json"))


# ── redaction on the real transport (GK B4 review) ─────────────────────────


@pytest.fixture()
def local_entsoe(monkeypatch):
    """A plain-HTTP server on localhost standing in for the fixed host, so a
    request runs through httpx's real transport and httpcore, with their DEBUG
    logging. Only the module constant moves; the code path is the production one."""
    import http.server
    import threading

    body = _fixture()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            code = 401 if "bad" in self.path else 200
            self.send_response(code)
            self.send_header("Content-Length", str(len(body) if code == 200 else 0))
            self.end_headers()
            if code == 200:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(M, "BASE_URL", f"http://127.0.0.1:{server.server_port}/api")
    yield server
    server.shutdown()


def test_no_logger_at_any_level_sees_the_token(local_entsoe, caplog):
    caplog.set_level(logging.DEBUG)  # the root logger, every child propagating to it
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    series, _ = M.fetch_day_ahead("DE-LU", 2024, token=TOKEN)
    assert len(series) == 8784
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead("DE-LU", 2024, token=TOKEN + "bad")
    assert e.value.kind == "bad_token"
    assert any(
        r.name.startswith("httpcore") for r in caplog.records
    )  # DEBUG really ran
    assert TOKEN not in caplog.text
    assert "securityToken=<redacted>" in caplog.text


def test_a_network_refusal_carries_no_request_in_its_traceback(caplog, monkeypatch):
    import traceback

    monkeypatch.setattr(M, "BASE_URL", "http://127.0.0.1:9/api")  # nothing listens
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead("DE-LU", 2024, token=TOKEN)
    assert e.value.kind == "unavailable"
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert TOKEN not in "".join(traceback.format_exception(e.value))
    logging.getLogger("tests.market_prices").exception("refused", exc_info=e.value)
    assert TOKEN not in caplog.text


def test_an_unformattable_record_is_dropped_not_passed_on():
    record = logging.LogRecord("httpx", logging.INFO, "f", 1, "%s %s", ("one",), None)
    assert M._FILTER.filter(record) is False
    plain = logging.LogRecord("httpx", logging.INFO, "f", 1, "GET /x", (), None)
    assert M._FILTER.filter(plain) is True and plain.getMessage() == "GET /x"


# ── the edges the mutant run found untested ────────────────────────────────


def test_a_pin_that_is_not_a_number_has_no_zone():
    assert M.zone_for_pin("52.5", 13.4) is None
    assert M.zone_for_pin(52.5, "13.4") is None
    assert M.zone_for_pin(52.5, None) is None


def test_a_core_holds_its_west_and_south_edges_not_its_east_and_north(monkeypatch):
    monkeypatch.setattr(M, "CORES", {"FR": ((0.0, 0.0, 1.0, 1.0),)})
    assert M.zone_for_pin(0.0, 0.0) == "FR"
    assert M.zone_for_pin(0.5, 0.999) == "FR"
    assert M.zone_for_pin(1.0, 0.5) is None  # north edge
    assert M.zone_for_pin(0.5, 1.0) is None  # east edge


def test_pin_of_tolerates_a_document_without_sites_and_a_bad_lng():
    assert M.pin_of({}) is None
    assert M.pin_of({"sites": None}) is None
    assert M.pin_of({"sites": [{"origin": {"lat": 1.0, "lng": "x"}}]}) is None


def test_without_now_the_clock_decides():
    assert M.last_complete_year() == datetime.now(UTC).year - 1
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    _, _, fetched = M.day_ahead(
        "DE-LU", 2024, transport=Replay(body=_fixture()).transport
    )
    assert fetched == datetime.now(UTC).date().isoformat()


def _swap(old: bytes, new: bytes) -> bytes:
    body = _doc(_year_prices(2023))
    assert old in body
    return body.replace(old, new, 1)


@pytest.mark.parametrize(
    "body, words",
    [
        (b"<Publication_MarketDocument", "could not be read"),
        (b"<?xml version='1.0'?><Other_MarketDocument/>", "not a price list"),
    ],
)
def test_an_unreadable_or_foreign_document_is_refused(body, words):
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body, "DE-LU", 2023)
    assert e.value.kind == "bad_document" and words in e.value.message


@pytest.mark.parametrize(
    "old, new, words",
    [
        (
            b"<start>2023-01-01T23:00Z</start>",
            b"<start>2023-01-01T23:00</start>",
            "unreadable time",
        ),  # naive: no Z
        (
            b"<start>2022-12-31T23:00Z</start>\n        <end>",
            b"<start>2022-12-31T23:00Z</start>\n        <end>x",
            "unreadable time",
        ),
        (
            b"<price_Measure_Unit.name>MWH</price_Measure_Unit.name>",
            b"",
            "unit other than MWh",
        ),
        (b"<position>2</position>", b"<position></position>", "not a number"),
        (b"<price.amount>1</price.amount>", b"", "not a number"),
        (
            b"<price.amount>1</price.amount>",
            b"<price.amount>NaN</price.amount>",
            "outside its period",
        ),
        (
            b"<position>1</position>",
            b"<position>2</position>",
            "without its first price",
        ),
    ],
)
def test_a_malformed_period_is_refused_in_words(old, new, words):
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(_swap(old, new), "DE-LU", 2023)
    assert e.value.kind == "bad_document" and words in e.value.message


def test_an_empty_period_is_refused():
    raw = _doc(_year_prices(2023)).decode()
    i = raw.index("<end>2023-01-01T23:00Z</end>")
    body = (
        raw[:i]
        + "<end>2022-12-31T23:00Z</end>"
        + raw[i + len("<end>2023-01-01T23:00Z</end>") :]
    )
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body.encode(), "DE-LU", 2023)
    assert e.value.kind == "bad_document" and "empty price period" in e.value.message


def test_a_missing_curve_type_reads_as_a01():
    body = _doc(_year_prices(2023), curve="A01").replace(
        b"<curveType>A01</curveType>", b""
    )
    series, _ = M.parse_a44(body, "DE-LU", 2023)
    assert len(series) == 8760


def test_a_document_without_time_series_is_no_data():
    raw = _doc(_year_prices(2023)).decode()
    body = raw[: raw.index("<TimeSeries>")] + "</Publication_MarketDocument>\n"
    with pytest.raises(M.MarketPriceError) as e:
        M.parse_a44(body.encode(), "DE-LU", 2023)
    assert e.value.kind == "no_data"


def test_the_size_cap_is_a_ceiling_the_document_may_reach(monkeypatch):
    body = _fixture()
    monkeypatch.setattr(M, "MAX_RESPONSE_BYTES", len(body))
    assert len(M.parse_a44(body, "DE-LU", 2024)[0]) == 8784
    series, _ = M.fetch_day_ahead(
        "DE-LU", 2024, token=TOKEN, transport=Replay(body=body).transport
    )
    assert len(series) == 8784
    monkeypatch.setattr(M, "MAX_RESPONSE_BYTES", len(body) - 1)
    for call in (
        lambda: M.parse_a44(body, "DE-LU", 2024),
        lambda: M.fetch_day_ahead(
            "DE-LU", 2024, token=TOKEN, transport=Replay(body=body).transport
        ),
    ):
        with pytest.raises(M.MarketPriceError) as e:
            call()
        assert "more data than one year" in e.value.message


def test_429_says_ENTSO_E_is_limiting():
    with pytest.raises(M.MarketPriceError) as e:
        M.fetch_day_ahead(
            "DE-LU", 2024, token=TOKEN, transport=Replay(status=429).transport
        )
    assert "limiting requests" in e.value.message


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: {**d, "year": 2023},
        lambda d: {**d, "values": [float("nan")] + d["values"][1:]},
    ],
)
def test_a_cache_for_another_year_or_with_a_gap_refetches(damage):
    app_secrets.set_secret(M.TOKEN_NAME, TOKEN)
    r = Replay(body=_fixture())
    M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    path = M.cache_dir() / "DE-LU" / "2024.json"
    path.write_text(json.dumps(damage(json.loads(path.read_text()))))
    M.day_ahead("DE-LU", 2024, transport=r.transport, now=NOW)
    assert len(r.requests) == 2


def test_the_cache_keeps_the_notes():
    series = pd.Series(1.0, index=_hours(2024))
    M._write_cache(
        "DE-LU", 2024, series, ["resolution:PT15M averaged to hourly"], "2026-11-21"
    )
    _, notes, fetched = M._read_cache("DE-LU", 2024)
    assert notes == ["resolution:PT15M averaged to hourly"] and fetched == "2026-11-21"
