"""
Build the ENTSO-E A44 fixtures `tests/test_market_prices.py` replays (SCU06 S1).

    python tests/fixtures/entsoe/build.py

`de_lu_2024_a44.xml.gz` is CONSTRUCTED, not recorded: the observed DE-LU 2024
day-ahead prices the IPP template ships (SMARD, CC BY 4.0,
`project_templates/eh_ipp_hybrid/sources/smard_de_lu_2024.csv`) written in the
A44 Publication_MarketDocument layout (namespace 7:3, one TimeSeries per
delivery day in CET/CEST, so the DST days have 23 and 25 points, curve type
A03 with repeated prices left out). The hour before 2024 and the hours after it
in the last delivery day are padded with the nearest value. Replace it with a
recorded response once a token is available (the live probe in
`services/market_prices.py`).
"""
from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SEED = HERE.parents[2] / "project_templates" / "eh_ipp_hybrid" / "sources" / "smard_de_lu_2024.csv"
NS = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"
EIC = "10Y1001A1001A82H"


def _z(t: pd.Timestamp) -> str:
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


def a44(prices: pd.Series, eic: str = EIC, resolution: str = "PT60M", curve: str = "A03") -> str:
    """`prices` (UTC-indexed, regular) → an A44 document, one TimeSeries per CET day."""
    step = pd.Timedelta(resolution.replace("PT", "").replace("M", "min"))
    local = prices.tz_convert("Europe/Brussels")
    out = [f'<?xml version="1.0" encoding="utf-8"?>\n<Publication_MarketDocument xmlns="{NS}">',
           "  <mRID>fixture</mRID>\n  <revisionNumber>1</revisionNumber>\n  <type>A44</type>",
           f"  <period.timeInterval>\n    <start>{_z(prices.index[0])}</start>\n"
           f"    <end>{_z(prices.index[-1] + step)}</end>\n  </period.timeInterval>"]
    for i, (_day, day) in enumerate(local.groupby(local.index.date), start=1):
        out += [f"  <TimeSeries>\n    <mRID>{i}</mRID>\n    <auction.type>A01</auction.type>",
                "    <businessType>A62</businessType>",
                f'    <in_Domain.mRID codingScheme="A01">{eic}</in_Domain.mRID>',
                f'    <out_Domain.mRID codingScheme="A01">{eic}</out_Domain.mRID>',
                "    <contract_MarketAgreement.type>A01</contract_MarketAgreement.type>",
                "    <currency_Unit.name>EUR</currency_Unit.name>",
                "    <price_Measure_Unit.name>MWH</price_Measure_Unit.name>",
                f"    <curveType>{curve}</curveType>\n    <Period>\n      <timeInterval>",
                f"        <start>{_z(day.index[0])}</start>\n        <end>{_z(day.index[-1] + step)}</end>",
                f"      </timeInterval>\n      <resolution>{resolution}</resolution>"]
        last = None
        for pos, v in enumerate(day.values, start=1):
            if curve == "A03" and v == last:
                continue
            out.append(f"      <Point>\n        <position>{pos}</position>\n"
                       f"        <price.amount>{v:g}</price.amount>\n      </Point>")
            last = v
        out.append("    </Period>\n  </TimeSeries>")
    out.append("</Publication_MarketDocument>\n")
    return "\n".join(out)


def seed() -> pd.Series:
    df = pd.read_csv(SEED)
    return pd.Series(df["value"].to_numpy(float), index=pd.DatetimeIndex(df["timestamp"]))


def main() -> None:
    s = seed()
    full = pd.date_range("2023-12-31T23:00Z", "2025-01-01T23:00Z", freq="h", inclusive="left")
    padded = s.reindex(full).ffill().bfill()
    (HERE / "de_lu_2024_a44.xml.gz").write_bytes(
        gzip.compress(a44(padded).encode("utf-8"), mtime=0))


if __name__ == "__main__":
    main()
