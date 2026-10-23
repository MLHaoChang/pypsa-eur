"""Build defaults-pack version 2026-10-24 from 2026-10-23 plus the electrified-heat rows (refinery gap 2).

Usage: python -I make_pack_2026-10-24.py <repo root> <costs_2030.csv> <process_heat.xlsx> <storage.xlsx>

The sources are technology-data v0.14.0's `outputs/costs_2030.csv` and two DEA workbooks from the same
tag's `inputs/` (technology_data_for_industrial_process_heat.xlsx, technology_data_catalogue_for_energy_storage.xlsx).
Every number is read from its source cell here; nothing is typed in by hand.
"""
import csv, json, shutil, sys
from pathlib import Path

import openpyxl

ROOT, TD_CSV, HEAT_XLSX, STORE_XLSX = (Path(a) for a in sys.argv[1:5])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-10-23", "2026-10-24"
TAG = "https://raw.githubusercontent.com/PyPSA/technology-data/refs/tags/v0.14.0"
TD_URL = f"{TAG}/outputs/costs_2030.csv"
HEAT_URL = f"{TAG}/inputs/technology_data_for_industrial_process_heat.xlsx"
STORE_URL = f"{TAG}/inputs/technology_data_catalogue_for_energy_storage.xlsx"
TD_SRC = "Danish Energy Agency, inputs/technology_data_for_industrial_process_heat.xlsx"
HEAT_SRC = "Danish Energy Agency, Technology Data for Industrial Process Heat, sheet {sheet!r}, row {row!r}, 2030"
STORE_SRC = "Danish Energy Agency, Technology Data for Energy Storage, sheet {sheet!r}, row {row!r}, 2030"
# technology-data v0.14.0 config.yaml: eur_year 2025, so costs_2030.csv is in 2025 EUR.
TD_MONEY_YEAR = "2025"

td = {}
with TD_CSV.open(encoding="utf-8", newline="") as fh:
    for r in csv.DictReader(fh):
        td[(r["technology"], r["parameter"])] = r


def sheet_rows(path, sheet):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)[sheet]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def dea_heat(sheet, label):
    """(2030 value, 2030 lower, 2030 upper) of the process-heat row whose label starts with `label`."""
    rows = sheet_rows(HEAT_XLSX, sheet)
    head = next(r for r in rows if r and r[0] == "Energy/technical data")
    c30 = head.index(2030)
    cu = head.index("Uncertainty (2030)")
    row = next(r for r in rows if isinstance(r[0], str) and r[0].strip().startswith(label))
    lo, hi = row[cu], row[cu + 1]
    num = lambda x: x if isinstance(x, (int, float)) else None
    return row[c30], num(lo), num(hi), row[0].strip()


def dea_store(sheet, label):
    """2030 value of the storage row whose label (column 0 or 1) starts with `label`; the catalogue
    states its uncertainty for 2025 and 2050 only, so no 2030 range."""
    rows = sheet_rows(STORE_XLSX, sheet)
    head = next(r for r in rows if r and r[0] == "year")
    c30 = head.index(2030)
    for r in rows:
        for c in (0, 1):
            if isinstance(r[c], str) and r[c].strip().startswith(label):
                return r[c30], r[c].strip()
    raise SystemExit(f"{sheet}: no row {label!r}")


TECH = {
    "electric-boiler-steam": dict(
        label="Electric (electrode) steam boiler, 10 kV (per MW of heat output)",
        basis="per_MW", capacity_basis="output_product", grid="unknown",
        source_technology="electric boiler steam", sheet="310.1 Electric boiler steam  "),
    "industrial-heat-pump-ht": dict(
        label="High-temperature industrial heat pump, up to 150 degC (per MW of heat output)",
        basis="per_MW", capacity_basis="output_product", grid="unknown",
        source_technology="industrial heat pump high temperature", sheet="302.b High temp. hp Up to 150"),
    "electric-process-heater": dict(
        label="Electric process heater, direct firing (per MW of heat output)",
        basis="per_MW", capacity_basis="output_product", grid="unknown",
        source_technology="Direct firing, Electricity", sheet="312.c Direct firing Electricity"),
    "thermal-storage-molten-salt": dict(
        label="Molten-salt heat store for industrial steam (per MWh of storage)",
        basis="per_MWh", capacity_basis="storage_energy", grid="false",
        source_technology="Molten salt Carnot battery", sheet="143b Molten salt Carnot battery"),
}
DOMAIN = {"overnight": "[0, inf)", "fom_share": "[0, 1]", "lifetime": "[1, inf)",
          "efficiency": "(0, 1]", "min_load": "[0, 1]", "max_sink_temperature": "[0, inf)",
          "steam_temperature": "[0, inf)"}
WHAT = {"overnight": "overnight cost", "fom_share": "fixed O&M (share of overnight cost per year)",
        "lifetime": "lifetime", "min_load": "minimum load (share of full load)"}


def row(tech, parameter, value, original_unit, unit, *, lo=None, hi=None, range_source=None,
        money=False, money_year="", source, url, what=None, domain=None, note="", cap=None):
    t = TECH[tech]
    if lo is None and hi is None and range_source is None:          # the pack's +-30 % assumed band
        lo, hi, range_source = round(value * 0.7, 4), round(value * 1.3, 4), "assumed"
        if cap is not None:                                         # clipped to the domain, as battery's
            hi = min(hi, cap)
    return {
        "key": f"{tech}.investment.{parameter}", "technology": tech, "part": "investment",
        "basis": t["basis"], "capacity_basis": t["capacity_basis"],
        "includes_grid_connection": t["grid"], "source_technology": t["source_technology"],
        "parameter": parameter, "label": f"{t['label'].split(' (per ')[0]} {what or WHAT[parameter]}",
        "original_value": repr(float(value)), "original_unit": original_unit, "unit": unit,
        "range_low": "" if lo is None else repr(float(lo)), "range_high": "" if hi is None else repr(float(hi)),
        "range_source": range_source or "", "currency": "EUR" if money else "",
        "money_year": money_year if money else "", "price_basis": "real", "source": source,
        "source_year": "2026", "source_url": url, "projection_year": "2030",
        "domain": domain or DOMAIN[parameter], "illustrative": "false", "derived_inputs": "",
        "formula_id": "", "note": note,
    }


def td_rows(tech, *, eff_what, eff_domain, eff_range_label):
    t = TECH[tech]
    g = lambda p: td[(t["source_technology"], p)]
    assert g("investment")["source"] == TD_SRC and g("investment")["unit"] == "EUR/kW", g("investment")
    sheet = t["sheet"]
    life, llo, lhi, _ = dea_heat(sheet, "Technical lifetime")
    eff, elo, ehi, elabel = dea_heat(sheet, eff_range_label)
    assert float(g("lifetime")["value"]) == life and abs(float(g("efficiency")["value"]) - eff / 100) < 1e-9
    out = [
        row(tech, "overnight", float(g("investment")["value"]), "EUR/kW", "EUR/MW", money=True,
            money_year=TD_MONEY_YEAR, source=TD_SRC, url=TD_URL,
            note=f"technology-data: {g('investment')['further description'].strip()}; per MW of heat output"),
        row(tech, "fom_share", float(g("FOM")["value"]), "%/year", "share/year", source=TD_SRC, url=TD_URL),
        row(tech, "lifetime", life, "years", "years", lo=llo, hi=lhi,
            range_source="source", source=TD_SRC, url=TD_URL,
            note=f"range: DEA sheet {sheet.strip()!r}, Technical lifetime (years), Uncertainty (2030)"),
        row(tech, "efficiency", float(g("efficiency")["value"]), "per unit", "per unit",
            lo=elo / 100, hi=ehi / 100, range_source="source", source=TD_SRC, url=TD_URL,
            note=f"range: DEA sheet {sheet.strip()!r}, {elabel}, Uncertainty (2030)",
            what=eff_what, domain=eff_domain),
    ]
    return out


rows_new = []
# 1. Electric steam boiler (DEA 310.1 via technology-data)
rows_new += td_rows("electric-boiler-steam", eff_what="efficiency (heat out per electricity in, annual average)",
                    eff_domain="(0, 1]", eff_range_label="Total efficiency, net (%), annual average")
ml, *_ , lbl = dea_heat(TECH["electric-boiler-steam"]["sheet"], "Minimum load")
rows_new.append(row("electric-boiler-steam", "min_load", ml, "%", "per unit",
                    source=HEAT_SRC.format(sheet="310.1 Electric boiler steam", row=lbl), url=HEAT_URL,
                    note="DEA states no steam pressure or temperature; unit size 10-60 MW (note A)"))
# 2. High-temperature heat pump (DEA 302.b via technology-data). COP in the efficiency row.
rows_new += td_rows("industrial-heat-pump-ht", eff_what="COP (heat out per electricity in, annual average)",
                    eff_domain="[1, inf)", eff_range_label="Total efficiency, net (%), annual average")
hp = TECH["industrial-heat-pump-ht"]["sheet"]
ml, *_ , lbl = dea_heat(hp, "Minimum load")
sup, *_ = dea_heat(hp, "Temperature heat source supply")
ret, *_ = dea_heat(hp, "Temperature heat source return")
rows_new.append(row("industrial-heat-pump-ht", "min_load", ml, "%", "per unit",
                    source=HEAT_SRC.format(sheet=hp, row=lbl), url=HEAT_URL,
                    note="DEA note E: theoretically as low as 10 %, for a screw compressor 25 %"))
rows_new.append(row("industrial-heat-pump-ht", "max_sink_temperature", 150, "degC", "degC",
                    lo=None, hi=None, range_source="", source=HEAT_SRC.format(sheet=hp, row="Technology")
                    + " (High temperature heat pumps, up to 150 degC)", url=HEAT_URL,
                    what="maximum supply (sink) temperature",
                    note=f"COP basis: heat source supply {sup} degC, return {ret} degC (DEA 302.b)"))
# 3. Electric process heater (DEA 312.c, transcribed from DEA; not in technology-data). 2019 EUR.
ph = TECH["electric-process-heater"]["sheet"]
src = lambda lbl: HEAT_SRC.format(sheet=ph, row=lbl)
inv, _, _, lbl = dea_heat(ph, "Nominal investment")
rows_new.append(row("electric-process-heater", "overnight", inv, "MEUR/MW", "EUR/MW", money=True,
                    money_year="2019", source=src(lbl), url=HEAT_URL,
                    note="DEA index: all cost data in 2019 EUR; note E: unit price from a supplier, installed "
                         "price assessed by DEA; per MW of heat output"))
fom, _, _, lbl = dea_heat(ph, "Fixed O&M")
assert fom == 0, fom
rows_new.append(row("electric-process-heater", "fom_share", 0.0, "%/year", "share/year", lo=0.0, hi=0.0,
                    range_source="source", source=src(lbl), url=HEAT_URL,
                    note="DEA states Fixed O&M 0 EUR/MW/year (note F: units of many heating elements, each "
                         "replaced on failure), so 0 % of the overnight cost"))
life, llo, lhi, lbl = dea_heat(ph, "Technical lifetime")
rows_new.append(row("electric-process-heater", "lifetime", life, "years", "years", lo=llo, hi=lhi,
                    range_source="source", source=src(lbl), url=HEAT_URL))
eff, elo, ehi, lbl = dea_heat(ph, "Total efficiency, net (%), annual average")
rows_new.append(row("electric-process-heater", "efficiency", eff, "%", "per unit", lo=elo, hi=ehi,
                    range_source="source", source=src(lbl), url=HEAT_URL,
                    what="efficiency (heat out per electricity in, annual average)",
                    note="DEA note A: transformer losses not included"))
ml, *_, lbl = dea_heat(ph, "Minimum load")
rows_new.append(row("electric-process-heater", "min_load", ml, "%", "per unit", source=src(lbl), url=HEAT_URL,
                    note="DEA note C: depends on the flow conditions; DEA states no temperature reach"))
# 4. Molten-salt heat store for industrial steam (DEA 143b). 2020 EUR as the sheet states.
ms = TECH["thermal-storage-molten-salt"]["sheet"]
ssrc = lambda lbl: STORE_SRC.format(sheet=ms, row=lbl)
inv, lbl = dea_store(ms, "Specific investment")
rows_new.append(row("thermal-storage-molten-salt", "overnight", inv, "kEUR/MWh", "EUR/MWh", money=True,
                    money_year="2020", source=ssrc(lbl), url=STORE_URL,
                    note="one cost per MWh of storage covering the resistive charge heaters, salt tanks and "
                         "steam generator (2030 example: 800 MWh, 120 MW in, 50 MW out); DEA note K: local grid "
                         "connection and reinforcement not included"))
fom, lbl = dea_store(ms, "Fixed O&M")
rows_new.append(row("thermal-storage-molten-salt", "fom_share", fom, "%/year", "share/year", source=ssrc(lbl),
                    url=STORE_URL, note="DEA note G: large uncertainty"))
life, lbl = dea_store(ms, "Technical lifetime")
rows_new.append(row("thermal-storage-molten-salt", "lifetime", life, "years", "years", source=ssrc(lbl),
                    url=STORE_URL, note="DEA note H: large uncertainty"))
eff, lbl = dea_store(ms, "System efficiency PtH")
ch, _ = dea_store(ms, "- Charge efficiency")
dis, _ = dea_store(ms, "- Discharge efficiency")
rows_new.append(row("thermal-storage-molten-salt", "efficiency", eff, "%", "per unit", cap=100, source=ssrc(lbl),
                    url=STORE_URL, what="power-to-heat efficiency (electricity in to steam out)",
                    note=f"charge {ch} %, discharge {dis} %; DEA note D: includes 0.75 % loss over a 24-48 h "
                         "storage period"))
st, lbl = dea_store(ms, "Steam temperature")
rows_new.append(row("thermal-storage-molten-salt", "steam_temperature", st, "degC", "degC", lo=None, hi=None,
                    range_source="", source=ssrc(lbl), url=STORE_URL, what="steam supply temperature",
                    note="DEA states 250-550 degC as the 2025 uncertainty range"))

dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
with (dst / "values.csv").open(encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh))
header = list(rows[0])
assert not {r["technology"] for r in rows} & set(TECH)
rows += rows_new
with (dst / "values.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n"); w.writeheader(); w.writerows(rows)
m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
m["seeded_from"]["heat_technologies"] = {
    "library_version": "technology-data v0.14.0 (eur_year 2025) and the Danish Energy Agency catalogues it ships",
    "urls": [TD_URL, HEAT_URL, STORE_URL],
    "rows": ("one `investment` part each: overnight, fom_share, lifetime, efficiency (a COP for the heat pump) "
             "and the operating range the source states (min_load, temperatures); electric-boiler-steam <- "
             "electric boiler steam (DEA 310.1), industrial-heat-pump-ht <- industrial heat pump high temperature "
             "(DEA 302.b), both from costs_2030.csv in 2025 EUR; electric-process-heater <- DEA 312.c Direct "
             "firing, Electricity, 2019 EUR; thermal-storage-molten-salt <- DEA 143b Molten salt Carnot battery "
             "(industrial steam), 2020 EUR"),
    "why": ("refinery electrification gap 2 (.scratch/grid-to-process/research/se-electrifying-refining-desw024.md "
            "section 6a.3): library rows for the electrified heat technologies"),
}
m["heat_technologies"] = {"from": PREV, "why": f"{len(rows_new)} new rows; every 2026-10-23 row, the finance "
                          "defaults, tariffs and load profiles are unchanged"}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", len(rows_new))
