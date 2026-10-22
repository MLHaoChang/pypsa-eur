"""
Fetch the IPP example's two observed day-ahead price years ONCE (IPP renewables + BESS GS-4;
owner Q-GS4 = A, 2026-10-21). Not run by the app or CI: its output is committed under
`eh_ipp_hybrid/sources/` and pinned by hash in `tests/test_eh_ipp_observed_prices.py`.

* SMARD (Bundesnetzagentur | SMARD.de, CC BY 4.0): DE-LU day-ahead price, filter 4169,
  hourly, from the weekly chart-data files.
* Energinet (www.energidataservice.dk, CC BY 4.0): DK1 spot price, the `Elspotprices`
  dataset (hourly; the newer `DayAheadPrices` starts in the 15-minute era, after 2024).

Each source is written as `timestamp` (UTC, ISO) and `value` (EUR/MWh), one row per hour of
2024 (8784, leap year). GET only, to two fixed hosts.

    python project_templates/fetch_ipp_market_series.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.request

import pandas as pd

YEAR = 2024
OUT = pathlib.Path(__file__).resolve().parent / "eh_ipp_hybrid" / "sources"
SMARD = "https://www.smard.de/app/chart_data/4169/DE-LU"
ENERGINET = "https://api.energidataservice.dk/dataset/Elspotprices"
AXIS = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31 23:00", freq="h")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "pypsa-gui IPP example fetch"})
    with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https hosts
        return json.loads(r.read().decode())


def smard() -> pd.Series:
    stamps = _get(f"{SMARD}/index_hour.json")["timestamps"]
    lo = pd.Timestamp(f"{YEAR}-01-01", tz="UTC") - pd.Timedelta(days=8)
    hi = pd.Timestamp(f"{YEAR + 1}-01-01", tz="UTC")
    rows: dict[pd.Timestamp, float] = {}
    for t in stamps:
        if not lo <= pd.Timestamp(t, unit="ms", tz="UTC") < hi:
            continue
        for ms, v in _get(f"{SMARD}/4169_DE-LU_hour_{t}.json")["series"]:
            if v is not None:
                rows[pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(None)] = float(v)
    return pd.Series(rows).sort_index().reindex(AXIS)


def energinet() -> pd.Series:
    q = (f"{ENERGINET}?start={YEAR}-01-01T00:00&end={YEAR + 1}-01-01T00:00"
         "&filter=%7B%22PriceArea%22:%5B%22DK1%22%5D%7D&timezone=UTC&limit=0")
    recs = _get(q)["records"]
    s = pd.Series({pd.Timestamp(r["HourUTC"]): float(r["SpotPriceEUR"]) for r in recs})
    return s.sort_index().reindex(AXIS)


def write(name: str, s: pd.Series) -> str:
    if s.isna().any():
        raise SystemExit(f"{name}: {int(s.isna().sum())} hour(s) of {YEAR} missing")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    pd.DataFrame({"timestamp": s.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "value": s.round(2).to_numpy()}).to_csv(path, index=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    for name, fetch in (("smard_de_lu_2024.csv", smard), ("energinet_dk1_2024.csv", energinet)):
        print(name, write(name, fetch()))


if __name__ == "__main__":
    main()
