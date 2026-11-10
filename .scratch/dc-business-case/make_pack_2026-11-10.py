"""Build defaults-pack version 2026-11-10 from 2026-11-07: the data-centre business-case cost defaults (DC BC 02).

Usage: python -I make_pack_2026-11-10.py <repo root>

Every 2026-11-07 file is copied byte for byte; `values.csv` gains rows appended after its last line, so each
2026-11-07 row stays byte-equal (GK B BC9, the AS-02 rule). SAGC-1: public sources and generic engineering only.
Every new value is assumed (`illustrative` true) with a range; revenue prices get no row (DCBC-SCOPE-1 Q4).

One technology, `dc-business-case`, with no investment part, so no `CostPart` appears and no template, study or
pricing path that enumerates the pack's priced parts sees it. Keys are read by `models.business_case` only:

* `dc-business-case.facility.overnight`  building, shell and M&E per MW of IT (EUR/MW, money year 2025)
* `dc-business-case.facility.lifetime`   its straight-line life (years)
* `dc-business-case.facility.maintenance_share`  non-energy maintenance, share of facility capex per year
* `dc-business-case.it_hardware.lifetime` GPU/server straight-line life (years), 5 (4-6)
* `dc-business-case.insurance_share`     share of capex per year
* `dc-business-case.property_tax_share`  share of asset value per year
* `dc-business-case.sga_share`           SG&A, share of revenue (per unit)

No row: IT hardware capex (no primary price list), staff and network per MW-year (no primary per-MW source,
research.md §2c); the model leaves those not established until the user states them.
"""
import csv, io, json, shutil, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-11-07", "2026-11-10"
TECH = "dc-business-case"

USD_PER_EUR_2025 = 1.13   # ECB euro reference rate, 2025 annual average (as published by the Bank of Finland)
FX = f"converted at {USD_PER_EUR_2025} USD/EUR (ECB euro reference rate, 2025 annual average)"
JLL = "JLL, Global Data Center Outlook (January 2026)"
CW = "Cushman & Wakefield, Data Center Development Cost Guide 2026 (via trade press, not verified at source)"
DLR = "Digital Realty Trust, Form 10-K FY2025"
EQIX = "Equinix, Form 10-K FY2025"
CRWV = "CoreWeave, Form 10-K FY2025"
HYPER = ("Microsoft, Alphabet, Amazon and Meta Forms 10-K FY2025 (server lives 5 to 6 years); "
         "Amazon shortened a subset to 5 years from 2025")
RESEARCH = ".scratch/dc-business-case/research.md"


def eur_per_kw(musd_per_mw: float) -> float:
    return round(musd_per_mw * 1000.0 / USD_PER_EUR_2025, 1)


dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
src = (V / PREV / "values.csv").read_bytes()
header = src.split(b"\n", 1)[0].decode().split(",")


def row(key: str, **kw):
    base = {k: "" for k in header}
    base.update(key=f"{TECH}.{key}", technology=TECH, capacity_basis="not_applicable",
                includes_grid_connection="false", price_basis="real", source_year="2026",
                illustrative="true", range_source="assumed")
    base.update({k: str(v) for k, v in kw.items()})
    return base


new = [
    row("facility.overnight", parameter="overnight",
        label="Data-centre building, shell and M&E capex per MW of IT (excluding IT hardware)",
        original_value=eur_per_kw(10.7), original_unit="EUR/kW", unit="EUR/MW",
        range_low=eur_per_kw(8.9), range_high=eur_per_kw(23.3), currency="EUR", money_year="2025",
        source=f"assumed; {JLL}: USD 10.7 M/MW global average 2025; range {CW}: USD 8.9-23.3 M/MW; {FX}",
        domain="[0, inf)",
        note=f"Per MW of IT. Building, shell, mechanical and electrical fit-out; IT hardware, land and the "
             f"grid connection excluded. The energy assets the network prices are not inside ({RESEARCH} §2b)"),
    row("facility.lifetime", parameter="lifetime", label="Data-centre facility straight-line life",
        original_value=20, original_unit="years", unit="years", range_low=15, range_high=30,
        source=f"assumed; {DLR} (buildings and improvements 5-39 years, machinery and equipment 7-15); "
               f"{EQIX} (buildings 12-60 years)",
        domain="[1, inf)", note=f"One blended life for shell (30-40 years) and M&E (15-20 years) ({RESEARCH} §2a)"),
    row("facility.maintenance_share", parameter="maintenance_share",
        label="Non-energy maintenance (share of facility capex per year)",
        original_value=1.5, original_unit="%/year", unit="share/year", range_low=1.0, range_high=3.0,
        source=f"assumed; no primary per-MW source; {DLR} reports rental property operating and maintenance "
               f"inside 41% of revenue",
        domain="[0, 1]", note=f"Order of magnitude only ({RESEARCH} §2c)"),
    row("it_hardware.lifetime", parameter="lifetime", label="IT hardware (GPUs, servers) straight-line life",
        original_value=5, original_unit="years", unit="years", range_low=4, range_high=6,
        source=f"assumed; {HYPER}; {CRWV} (computing equipment 6 years)", domain="[1, inf)",
        note=f"Sensitivity 4-6 years (DCBC-SCOPE-1 Q1; {RESEARCH} §2a)"),
    row("insurance_share", parameter="insurance_share", label="Insurance (share of capex per year)",
        original_value=0.5, original_unit="%/year", unit="share/year", range_low=0.25, range_high=1.0,
        source=f"assumed; no primary per-MW source; {DLR} reports property tax and insurance together at "
               f"3.6% of revenue",
        domain="[0, 1]", note=f"Order of magnitude only ({RESEARCH} §2c)"),
    row("property_tax_share", parameter="property_tax_share",
        label="Property tax (share of asset value per year)",
        original_value=1.0, original_unit="%/year", unit="share/year", range_low=0.5, range_high=2.0,
        source=f"assumed; jurisdiction-specific; {DLR} reports property tax and insurance together at "
               f"3.6% of revenue",
        domain="[0, 1]", note=f"State the local rate where known ({RESEARCH} §2c)"),
    row("sga_share", parameter="sga_share", label="SG&A (share of revenue)",
        original_value=15, original_unit="%", unit="per unit", range_low=9, range_high=30,
        source=f"assumed; derived from {CRWV} (G&A 12.7% + S&M 2.8%), {DLR} (G&A 9.3%) and {EQIX} "
               f"(G&A 20.0% + S&M 9.8%)",
        domain="[0, 1]", note=f"Share of revenue, FY2025 filings ({RESEARCH} §2c)"),
]

buf = io.StringIO()
csv.DictWriter(buf, fieldnames=header, lineterminator="\n").writerows(new)
body = src if src.endswith(b"\n") else src + b"\n"
(dst / "values.csv").write_bytes(body + buf.getvalue().encode())

m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
m["dc_business_case_costs"] = {
    "from": NEW,
    "why": ("DC business case 02: researched non-energy cost defaults for the data-centre business case "
            "(facility capex, life and maintenance; IT hardware life; insurance; property tax; SG&A), each "
            "assumed with a source, a year and a range. No revenue price (DCBC-SCOPE-1 Q4). Every 2026-11-07 "
            "row, the finance defaults, tariffs and load profiles are unchanged"),
    "build_script": ".scratch/dc-business-case/make_pack_2026-11-10.py",
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(len(new), "rows added")
