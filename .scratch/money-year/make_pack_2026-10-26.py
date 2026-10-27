"""Build defaults-pack version 2026-10-26 from 2026-10-25: the two illustrative seed tariffs converted to 2025 EUR,
and the regional overlays 2026-10-26+<region> on that base.

Usage: python -I make_pack_2026-10-26.py <repo root>

Owner decision MONEY-YR-SEED-1 ("Convert to 2025"). The seed tariffs are 2020 EUR; the guided study now reads the
2025 technology-data rows (pack 2026-10-25), and `currency_year_mixed` refuses a case whose tariff and rows state
different years. Every money figure of the two tariffs (rates, `original_price`, export price) is multiplied by the
Eurostat HICP factor 2020 -> 2025 and is not rounded; finance.yaml's currency_year moves with them (the loader
requires the tariffs' year to equal the pack's). Every values.csv row, the finance defaults' values and the load
profiles are unchanged. The regional overlays copy the 2026-10-22+<region> rows unchanged and take the new base.
"""
import json, shutil, sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(sys.argv[1])
BACKEND = ROOT / "pypsa-gui/backend"
V = BACKEND / "services/library/defaults_pack/versions"
PREV, NEW, REGION_PREV = "2026-10-25", "2026-10-26", "2026-10-22"
REGIONS = ("ca_federal", "uk", "us_federal")
FROM_YEAR, TO_YEAR = 2020, 2025

# Eurostat prc_hicp_aind (HICP, annual average rate of change, all items), European Economic Area, 2021-2025: the
# file technology-data v0.14.0 vendors at inputs/prc_hicp_aind__custom_20097956_spreadsheet.xlsx.
INDEX_SOURCE = ("Eurostat prc_hicp_aind, HICP all items, annual average rate of change, European Economic Area "
                "(EEA), 2021-2025, as vendored by technology-data v0.14.0 at "
                "inputs/prc_hicp_aind__custom_20097956_spreadsheet.xlsx")
RATES = {"2021": "2.9", "2022": "9.2", "2023": "6.4", "2024": "2.6", "2025": "2.5"}   # percent
FACTOR = Decimal(1)
for pct in RATES.values():
    FACTOR *= 1 + Decimal(pct) / 100
FACTOR = FACTOR.normalize()
assert str(FACTOR) == "1.2573346011408", FACTOR
CONVERSION = (f"converted {FROM_YEAR} EUR -> {TO_YEAR} EUR in pack {NEW} (owner decision MONEY-YR-SEED-1): every "
              f"money figure x {FACTOR} = the product of (1 + rate) over {', '.join(f'{y} {r} %' for y, r in RATES.items())}"
              f" ({INDEX_SOURCE}); not rounded")


def conv(x) -> float:
    return float(Decimal(repr(x)) * FACTOR)


dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)

# tariffs.json: rates, original prices and export prices x FACTOR; currency_year and valid_from move to 2025.
tar_path = dst / "tariffs.json"
doc = json.loads(tar_path.read_text(encoding="utf-8"))
converted = {}
for entry in doc["tariffs"]:
    t, m = entry["tariff"], entry["meta"]
    tid = t["id"]
    assert m["currency"] == "EUR" and m["currency_year"] == FROM_YEAR, tid
    assert t["valid_from"] == f"{FROM_YEAR}-01-01", tid
    t["valid_from"] = f"{TO_YEAR}-01-01"
    m["currency_year"] = TO_YEAR
    for item in t["items"]:
        for p in item["periods"]:
            p["rate"] = conv(p["rate"])
    for rows in m["items"].values():
        for r in rows:
            r["original_price"] = conv(r["original_price"])
    m["export"]["price_per_mwh"] = conv(m["export"]["price_per_mwh"])
    m["source"] = f"{m['source']}; {CONVERSION}"
    old_rationale = f"valid_from {FROM_YEAR}-01-01 is the seed's money year"
    assert m["jurisdiction_rationale"].count(old_rationale) == 1, tid
    m["jurisdiction_rationale"] = m["jurisdiction_rationale"].replace(
        old_rationale, f"valid_from {TO_YEAR}-01-01 is the seed's money year (converted from {FROM_YEAR})")
    converted[tid] = "rates, original_price and export price_per_mwh"
assert sorted(converted) == ["de_industrial_illustrative", "tou_reference_illustrative"], converted
doc["_comment"] = (f"{doc['_comment']} Pack {NEW}: both tariffs are {TO_YEAR} EUR, {CONVERSION}.")
# The file's own layout (one item per line) is not kept: json.dumps writes it plainly.
tar_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# finance.yaml: currency_year 2025, with its comment and source.
fin = (dst / "finance.yaml").read_text(encoding="utf-8")
OLD_COMMENT = "# The finance defaults and the seed tariffs are in one currency year, 2020.\n"
NEW_COMMENT = (f"# The finance defaults and the seed tariffs are in one currency year, {TO_YEAR}\n"
               f"# (the seed tariffs converted from {FROM_YEAR} by the Eurostat HICP, EEA, factor\n"
               f"# {FACTOR}, in {NEW}; owner decision MONEY-YR-SEED-1).\n")
OLD_SOURCE = "  the seed tariffs' year (2020); every values.csv row states its own\n"
NEW_SOURCE = (f"  the seed tariffs' year ({TO_YEAR}: converted from {FROM_YEAR} x {FACTOR}, {INDEX_SOURCE});\n"
              f"  every values.csv row states its own\n")
OLD_YEAR = f"currency_year: {FROM_YEAR}\n"
assert fin.count(OLD_COMMENT) == 1 and fin.count(OLD_SOURCE) == 1 and fin.count(OLD_YEAR) == 1
fin = (fin.replace(OLD_COMMENT, NEW_COMMENT).replace(OLD_SOURCE, NEW_SOURCE)
       .replace(OLD_YEAR, f"currency_year: {TO_YEAR}\n"))
assert "No row is converted with a price\n# index." in fin
fin = fin.replace("No row is converted with a price\n# index.",
                  "No values.csv row is converted with a\n# price index.")
(dst / "finance.yaml").write_text(fin, encoding="utf-8")

m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
m["money_year_conversion"] = {
    "from": PREV,
    "why": ("owner decision MONEY-YR-SEED-1: the guided study reads the 2025 technology-data rows (pack 2026-10-25) "
            "and `currency_year_mixed` refuses a 2020 tariff against them, so the illustrative seed tariffs are "
            "converted to 2025 EUR"),
    "index": INDEX_SOURCE,
    "rates_percent": RATES,
    "factor": str(FACTOR),
    "converted": converted,
    "change": (f"the two tariffs: every money figure x {FACTOR} (not rounded), currency_year and valid_from "
               f"{FROM_YEAR} -> {TO_YEAR}; finance.yaml currency_year {FROM_YEAR} -> {TO_YEAR}; every values.csv "
               "row, the finance defaults' values and the load profiles are unchanged"),
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

sys.path.insert(0, str(BACKEND))
from services.library.defaults_pack import loader as L  # noqa: E402

base_hash = L._canonical_hash(L.canonical_payload(dst))
for region in REGIONS:
    rdst = V / f"{NEW}+{region}"
    if rdst.exists():
        shutil.rmtree(rdst)
    shutil.copytree(V / f"{REGION_PREV}+{region}", rdst)
    rm = json.loads((rdst / "manifest.json").read_text(encoding="utf-8"))
    assert rm["base_version"] == REGION_PREV, region
    rm.update(version=NEW, base_version=NEW, base_hash=base_hash)
    (rdst / "manifest.json").write_text(json.dumps(rm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", NEW, base_hash, FACTOR)
