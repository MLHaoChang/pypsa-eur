"""Build defaults-pack version 2026-10-30 from 2026-10-26: the split-storage technologies (AS-02).

Usage: python -I make_pack_2026-10-30.py <repo root>

Adds two technologies, each with a power part (per MW, AC side) and an energy part (per MWh), for the Store + Link
storage of `pypsa-gui/backend/services/solver/split_storage.py`. Every 2026-10-26 row, the finance defaults, the
tariffs and the load profiles are copied unchanged. SAGC-1: public sources and generic engineering only; every
range is "assumed".

* `supercapacitor`, from PNNL-28866 (Mongird et al., Energy Storage Technology and Cost Characterization Report,
  July 2019), 2018 USD:
  - energy part: the module at $32,365/kWh (section 4.3.11.1, E/P 0.0124 h, from two vendor quotes);
  - power part: PCS $350/kW + balance of plant $100/kW + construction and commissioning $80/kW (Table ES-1);
  - fixed O&M $1/kW-yr (4.3.11.2) as a share of the $930/kW total project cost; life 16 years and round-trip
    efficiency 0.92 (Table ES-1).
  Converted at the ECB 2018 annual average reference rate, 1.18095 USD/EUR; no price index (MONEY-YEAR-1), so the
  rows are 2018 EUR.
* `high-c-battery`: the pack's DEA `battery` rows, with the energy part x 1.75, the midpoint of PNNL-28866 section
  4.2.1's "lithium titanate oxide (LTO) systems had a 50-100 percent higher cost" than NMC; range 1.5-2.0 x.
"""
import csv, json, shutil, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-10-26", "2026-10-30"
PNNL_URL = ("https://www.energy.gov/sites/prod/files/2019/07/f65/"
            "Storage%20Cost%20and%20Performance%20Characterization%20Report_Final.pdf")
PNNL = "PNNL-28866, Mongird et al., Energy Storage Technology and Cost Characterization Report (July 2019)"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/A.USD.EUR.SP00.A"
USD_PER_EUR_2018 = 1.18095
LTO_LOW, LTO_MID, LTO_HIGH = 1.5, 1.75, 2.0

dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
with (dst / "values.csv").open(encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh))
header = list(rows[0])
by_key = {r["key"]: r for r in rows}


def band(value, cap=None):
    lo, hi = round(value * 0.7, 4), round(value * 1.3, 4)
    return repr(lo), repr(min(hi, cap) if cap is not None else hi)


SC = {"technology": "supercapacitor", "source_technology": "ultracapacitor", "includes_grid_connection": "unknown",
      "price_basis": "real", "source_year": "2019", "source_url": PNNL_URL, "projection_year": "2018",
      "illustrative": "false", "derived_inputs": "", "formula_id": "", "currency": "", "money_year": ""}
POWER = {"part": "power", "basis": "per_MW", "capacity_basis": "output_electric_ac"}
ENERGY = {"part": "energy", "basis": "per_MWh", "capacity_basis": "storage_energy"}
WHOLE = {"part": "", "basis": "", "capacity_basis": "not_applicable"}


def sc(part, parameter, label, value, original_unit, unit, domain, source, *, money=False, cap=None, note=""):
    lo, hi = band(value, cap)
    key = f"supercapacitor.{part['part']}.{parameter}" if part["part"] else f"supercapacitor.{parameter}"
    return {**SC, **part, "key": key, "parameter": parameter, "label": label, "original_value": repr(value),
            "original_unit": original_unit, "unit": unit, "range_low": lo, "range_high": hi,
            "range_source": "assumed", "domain": domain, "source": f"{PNNL}, {source}", "note": note,
            **({"currency": "EUR", "money_year": "2018"} if money else {})}


fx = (f"2018 USD converted at the ECB 2018 annual average reference rate {USD_PER_EUR_2018} USD/EUR ({ECB_URL}); "
      "no price index (MONEY-YEAR-1)")
new_rows = [
    sc(POWER, "overnight", "Supercapacitor power part (PCS, balance of plant, construction) overnight cost",
       round((350 + 100 + 80) / USD_PER_EUR_2018, 4), "EUR/kW", "EUR/MW", "[0, inf)",
       "Table ES-1: PCS $350/kW + BOP $100/kW + C&C $80/kW", money=True, note=fx),
    sc(ENERGY, "overnight", "Supercapacitor module (energy part) overnight cost",
       round(32365 / USD_PER_EUR_2018, 4), "EUR/kWh", "EUR/MWh", "[0, inf)",
       "section 4.3.11.1: $32,365/kWh at E/P 0.0124 h", money=True, note=fx),
    sc(POWER, "fom_share", "Supercapacitor fixed O&M (share of overnight cost per year)",
       round(100 * 1 / 930, 6), "%/year", "share/year", "[0, 1]",
       "section 4.3.11.2: $1/kW-yr over the Table ES-1 total project cost $930/kW",
       note="booked on the power part; the energy part carries none"),
    sc(POWER, "lifetime", "Supercapacitor power part lifetime", 16.0, "years", "years", "[1, inf)",
       "Table ES-1: life 16 years"),
    sc(ENERGY, "lifetime", "Supercapacitor module lifetime", 16.0, "years", "years", "[1, inf)",
       "Table ES-1: life 16 years"),
    sc(WHOLE, "round_trip_efficiency", "Supercapacitor round-trip efficiency", 0.92, "per unit", "per unit",
       "(0, 1]", "Table ES-1: system round-trip efficiency 0.92", cap=1.0),
]

hc_note = ("high-C-rate cells (LTO, high-power LFP) behind the battery's inverter; AS-02, research note "
           "ai-load-smoothing-sizing-loop.md 4.2.1")
for k in ("power.overnight", "power.fom_share", "power.efficiency", "power.lifetime", "energy.overnight",
          "energy.lifetime", "round_trip_efficiency"):
    b = by_key[f"battery.{k}"]
    r = {**b, "key": f"high-c-battery.{k}", "technology": "high-c-battery",
         "label": b["label"].replace("Battery", "High-C-rate battery", 1), "range_source": "assumed",
         "note": f"{hc_note}; copied from battery.{k}" + (f"; {b['note']}" if b["note"] else "")}
    if b["derived_inputs"]:
        r["derived_inputs"] = b["derived_inputs"].replace("battery.", "high-c-battery.")
    if k == "energy.overnight":
        v = float(b["original_value"])
        r.update(original_value=repr(round(v * LTO_MID, 4)), range_low=repr(round(v * LTO_LOW, 4)),
                 range_high=repr(round(v * LTO_HIGH, 4)), source=f"{b['source']}; x {LTO_MID}: {PNNL}, section "
                 f"4.2.1, LTO systems 50-100 percent above NMC", source_url=PNNL_URL,
                 note=f"{hc_note}; battery.energy.overnight x {LTO_MID} (range x {LTO_LOW}-{LTO_HIGH}), "
                      f"the battery's money year")
    new_rows.append(r)

assert not {r["key"] for r in new_rows} & set(by_key)
with (dst / "values.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n"); w.writeheader(); w.writerows(rows + new_rows)

m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
m["split_storage_technologies"] = {
    "from": PREV,
    "why": ("AS-02: storage as a Store (energy) with charger and discharger Links (power), so the supercapacitor "
            "and the high-C-rate battery are priced per MW and per MWh; every 2026-10-26 row, the finance "
            "defaults, tariffs and load profiles are unchanged"),
    "rows": [r["key"] for r in new_rows],
    "sources": {"supercapacitor": PNNL_URL, "usd_eur_2018": ECB_URL, "high-c-battery": PNNL_URL},
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{NEW}: {len(new_rows)} rows added")
