"""Build defaults-pack version 2026-11-07 from 2026-11-06: the data-centre power architectures (DC-05).

Usage: python -I make_pack_2026-11-07.py <repo root>

Every 2026-11-06 file is copied byte for byte; `values.csv` gains rows appended after its last line, so each
2026-11-06 row stays byte-equal (GK B K1, GK A C7). SAGC-1: public sources and generic engineering only.
Every new value is assumed (`illustrative` true, `range_source` assumed).

Four technologies, `dc-arch-ac`, `dc-arch-mv-ups`, `dc-arch-dc-800v`, `dc-arch-sst`:

* conversion-loss stages, one `efficiency` row each with no part (as `battery.round_trip_efficiency`):
  `dc-arch-<a>.stage.<stage>.efficiency`. The chain runs from the MV bus to the rack input (the PUE
  boundary: IT metered at the rack input, The Green Grid PUE category 3); the `rack` stage (server PSU, or the
  800 VDC to 54 VDC shelf) is beyond that boundary and changes IT energy, not PUE. Each stage's efficiency is
  flat over part load (assumed, GK A C4).
* one `investment` part per MW of IT (overnight, lifetime, fom_share), output_electric, without the grid
  connection. Boundary (GK B K3): MV bus to rack input, the in-rack conversion inside every row; IT equipment,
  building, cooling and the backup batteries (DC-03) excluded.
"""
import csv, io, json, shutil, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-11-06", "2026-11-07"

LBNL = "LBNL, Ton, Fortenbery and Tschudi, DC Power for Improved Data Center Efficiency (2008)"
SE127 = "Schneider Electric White Paper 127, A Quantitative Comparison of High Efficiency AC vs. DC Power Distribution for Data Centers"
TGG = "The Green Grid White Paper 49, PUE: A Comprehensive Examination of the Metric"
EU548 = "Commission Regulation (EU) No 548/2014 (Ecodesign, power transformers, Tier 2)"
EU548_URL = "https://eur-lex.europa.eu/eli/reg/2014/548/oj"
EU424 = "Commission Regulation (EU) 2019/424 (Ecodesign, servers and data storage, PSU efficiency); 80 PLUS Titanium"
EU424_URL = "https://eur-lex.europa.eu/eli/reg/2019/424/oj"
IEC = "IEC 62040-3 (UPS performance and efficiency classes)"
NV800 = "NVIDIA, 800 VDC architecture for AI factories (public technical note, 2025)"
SST = "Huber and Kolar, Solid-State Transformers: On the Origins and Evolution of Key Concepts, IEEE Industrial Electronics Magazine (2016)"

#: (architecture technology, label, [(stage, label, eta, low, high, source, url)], overnight EUR/kW (low, mid, high), fom)
ARCH = [
    ("dc-arch-ac", "AC (LV double-conversion UPS)", [
        ("mv_lv_transformer", "MV/LV transformer and LV feeders", 0.985, 0.98, 0.99, EU548, EU548_URL),
        ("ups", "LV double-conversion UPS", 0.955, 0.94, 0.97, IEC, ""),
        ("lv_distribution", "LV switchboard and PDU", 0.99, 0.985, 0.995, f"{LBNL}; {SE127}", ""),
        ("rack", "Server AC power supply (in rack)", 0.96, 0.94, 0.965, EU424, EU424_URL),
    ], (1000, 1600, 2400), 0.02),
    ("dc-arch-mv-ups", "MV UPS", [
        ("ups", "MV UPS", 0.98, 0.97, 0.985, IEC, ""),
        ("mv_lv_transformer", "MV/LV transformer and LV feeders", 0.985, 0.98, 0.99, EU548, EU548_URL),
        ("lv_distribution", "LV switchboard and PDU", 0.99, 0.985, 0.995, f"{LBNL}; {SE127}", ""),
        ("rack", "Server AC power supply (in rack)", 0.96, 0.94, 0.965, EU424, EU424_URL),
    ], (900, 1500, 2300), 0.02),
    ("dc-arch-dc-800v", "800 VDC (central rectifier)", [
        ("mv_lv_transformer", "MV/LV transformer and LV feeders", 0.985, 0.98, 0.99, EU548, EU548_URL),
        ("rectifier", "Central AC/800 VDC rectifier", 0.975, 0.965, 0.985, f"{NV800}; {LBNL}", ""),
        ("dc_busway", "800 VDC busway", 0.995, 0.99, 0.998, NV800, ""),
        ("rack", "800 VDC to 54 VDC shelf (in rack)", 0.975, 0.965, 0.98, NV800, ""),
    ], (900, 1450, 2400), 0.02),
    ("dc-arch-sst", "Solid-state transformer (MV AC to 800 VDC)", [
        ("sst", "Solid-state transformer, MV AC to 800 VDC", 0.975, 0.96, 0.985, SST, ""),
        ("dc_busway", "800 VDC busway", 0.995, 0.99, 0.998, NV800, ""),
        ("rack", "800 VDC to 54 VDC shelf (in rack)", 0.975, 0.965, 0.98, NV800, ""),
    ], (1200, 1900, 3400), 0.03),
]
LIFE = (15.0, 10.0, 20.0)
CAPEX_SOURCE = (f"assumed: order of magnitude of the electrical share of a data-centre build, relative AC/DC "
                f"capex from {LBNL} and {SE127}; SST premium for an immature technology")
CAPEX_NOTE = ("Per MW of IT. Boundary: MV bus to rack input, the in-rack conversion (PSU or 800 VDC shelf) "
              "inside; IT equipment, building, cooling, grid connection and the backup batteries excluded")

dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
src = (V / PREV / "values.csv").read_bytes()
header = src.split(b"\n", 1)[0].decode().split(",")


def row(**kw):
    base = {k: "" for k in header}
    base.update(price_basis="real", source_year="2026", illustrative="true", range_source="assumed")
    base.update({k: str(v) for k, v in kw.items()})
    return base


new = []
for tech, label, stages, (lo, mid, hi), fom in ARCH:
    for stage, slabel, eta, elo, ehi, source, url in stages:
        new.append(row(key=f"{tech}.stage.{stage}.efficiency", technology=tech, capacity_basis="not_applicable",
                       includes_grid_connection="false", parameter="efficiency",
                       label=f"{label}: {slabel} efficiency", original_value=eta, original_unit="per unit",
                       unit="per unit", range_low=elo, range_high=ehi, source=f"assumed; {source}",
                       source_url=url, domain="(0, 1]",
                       note=("beyond the PUE boundary (changes IT energy, not PUE)" if stage == "rack"
                             else "MV bus to rack input (PUE boundary, Green Grid category 3)")
                       + "; flat over part load (assumed)"))
    common = dict(technology=tech, part="investment", basis="per_MW", capacity_basis="output_electric",
                  includes_grid_connection="false", source_technology=tech)
    new.append(row(key=f"{tech}.investment.overnight", **common, parameter="overnight",
                   label=f"{label} power chain overnight cost (per MW of IT)", original_value=mid,
                   original_unit="EUR/kW", unit="EUR/MW", range_low=lo, range_high=hi, currency="EUR",
                   money_year="2025", source=CAPEX_SOURCE, domain="[0, inf)", note=CAPEX_NOTE))
    new.append(row(key=f"{tech}.investment.lifetime", **common, parameter="lifetime",
                   label=f"{label} power chain lifetime", original_value=LIFE[0], original_unit="years",
                   unit="years", range_low=LIFE[1], range_high=LIFE[2],
                   source=f"assumed: power-electronics service life; {IEC}", domain="[1, inf)"))
    new.append(row(key=f"{tech}.investment.fom_share", **common, parameter="fom_share",
                   label=f"{label} power chain fixed O&M (share of overnight cost per year)",
                   original_value=fom * 100, original_unit="%/year", unit="share/year",
                   range_low=fom * 50, range_high=fom * 150, source="assumed", domain="[0, 1]"))

buf = io.StringIO()
csv.DictWriter(buf, fieldnames=header, lineterminator="\n").writerows(new)
body = src if src.endswith(b"\n") else src + b"\n"
(dst / "values.csv").write_bytes(body + buf.getvalue().encode())

m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
m["dc_power_architectures"] = {
    "from": NEW,
    "why": ("DC-05: four data-centre power architectures (AC, MV UPS, 800 VDC, SST), each a conversion-loss "
            "chain (one efficiency row per stage, MV bus to rack input, plus the in-rack stage) and one "
            "investment part per MW of IT; every value assumed with a range. Every 2026-11-06 row, the "
            "finance defaults, tariffs and load profiles are unchanged"),
    "build_script": ".scratch/datacenter-grid-readiness/make_pack_2026-11-07.py",
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(len(new), "rows added")
