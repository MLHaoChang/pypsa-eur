"""Build defaults-pack version 2026-10-25 from 2026-10-24: the technology-data rows relabelled to 2025 money.

Usage: python -I make_pack_2026-10-25.py <repo root> <costs_2030.csv>

Owner decision MONEY-YEAR-1. technology-data v0.14.0's config.yaml states `eur_year: 2025`, so every value in its
`outputs/costs_2030.csv` is in 2025 EUR; the CSV's own `currency_year` column is the year of the source it inflated
from (its "central water tank storage" row says 2020 and holds 3.8174, DEA 141's 3.036 EUR2020 x 1.257). The pack's
money rows whose source_url is that CSV were labelled 2020; here they become 2025. Values are not touched: each is
asserted equal to the CSV's `investment` value. Rows transcribed from a DEA workbook keep their sheet's year.
"""
import csv, json, shutil, sys
from pathlib import Path

ROOT, TD_CSV = (Path(a) for a in sys.argv[1:3])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-10-24", "2026-10-25"
TD_URL = "https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0/outputs/costs_2030.csv"
TD_MONEY_YEAR = "2025"   # technology-data v0.14.0 config.yaml l.18: eur_year: 2025

with TD_CSV.open(encoding="utf-8", newline="") as fh:
    td = {(r["technology"], r["parameter"]): r for r in csv.DictReader(fh)}

dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
with (dst / "values.csv").open(encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh))
header = list(rows[0])
relabelled = []
for r in rows:
    if r["source_url"] != TD_URL or not r["currency"] or r["money_year"] == TD_MONEY_YEAR:
        continue
    assert r["parameter"] == "overnight" and r["money_year"] == "2020", r["key"]
    src = td[(r["source_technology"], "investment")]
    assert float(r["original_value"]) == float(src["value"]) and r["original_unit"] == src["unit"], r["key"]
    r["money_year"] = TD_MONEY_YEAR
    relabelled.append(r["key"])
assert len(relabelled) == 12, relabelled
with (dst / "values.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n"); w.writeheader(); w.writerows(rows)

# finance.yaml: currency_year 2020 stays (the seed tariffs' and finance defaults' year); its text, which said the
# technology-data rows were 2020 EUR, is corrected.
fin = (dst / "finance.yaml").read_text(encoding="utf-8")
OLD_COMMENT = """# The finance defaults and the seed tariffs are in one currency year, 2020:
# technology-data v0.14.0 states its cost rows in 2020 EUR. Every values.csv
# row states its own currency_year: 2020 for the catalogue rows, 2026 for the
# campus equipment rows (gridspine campus_assets.yaml price_year). No row is
# converted with a price index."""
NEW_COMMENT = """# The finance defaults and the seed tariffs are in one currency year, 2020.
# Every values.csv row states its own money_year: 2025 for the technology-data
# v0.14.0 rows (its config.yaml eur_year; relabelled in 2026-10-25, owner
# decision MONEY-YEAR-1), the DEA workbook's year for a row transcribed from
# one, 2025 for the campus equipment rows. No row is converted with a price
# index."""
OLD_SOURCE = """currency_year_source: >-
  technology-data v0.14.0 outputs/costs_2030.csv, column currency_year (2020
  on every catalogue row, and the seed tariffs' year); every values.csv row
  states its own currency_year (the campus equipment rows: 2026, the campus
  library's price_year)"""
NEW_SOURCE = """currency_year_source: >-
  the seed tariffs' year (2020); every values.csv row states its own
  money_year (the technology-data v0.14.0 rows: 2025, its eur_year; the
  campus equipment rows: 2025)"""
assert fin.count(OLD_COMMENT) == 1 and fin.count(OLD_SOURCE) == 1
(dst / "finance.yaml").write_text(fin.replace(OLD_COMMENT, NEW_COMMENT).replace(OLD_SOURCE, NEW_SOURCE),
                                  encoding="utf-8")

m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
for part in ("template_technologies", "ammonia_technologies"):
    text = m["seeded_from"][part]["rows"]
    assert text.count("in 2020 EUR") == 1, part
    m["seeded_from"][part]["rows"] = text.replace("in 2020 EUR", "in 2025 EUR (technology-data's eur_year)")
m["money_year_relabel"] = {
    "from": PREV,
    "why": ("owner decision MONEY-YEAR-1: technology-data v0.14.0 states eur_year 2025 (config.yaml), so its "
            "outputs/costs_2030.csv is in 2025 EUR; the CSV's currency_year column is the year each source was "
            "inflated from"),
    "relabelled": relabelled,
    "change": ("money_year 2020 -> 2025 on these rows only; every value, every other row (the DEA-workbook rows "
               "keep their sheet's year), the finance defaults, tariffs and load profiles are unchanged"),
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", len(relabelled))
