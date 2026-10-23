"""Build defaults-pack version 2026-10-23 from 2026-10-22 and the campus reference-cost proposal."""
import csv, json, re, shutil, sys, hashlib
from pathlib import Path
import yaml

ROOT = Path(sys.argv[1])
V = ROOT / "pypsa-gui/backend/services/library/defaults_pack/versions"
PREV, NEW = "2026-10-22", "2026-10-23"
prop = yaml.safe_load((ROOT / "docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml").read_text(encoding="utf-8"))
shipped = ROOT / ".scratch/campus-ic-extra-owner-assets/campus_assets.shipped.yaml"
KINDS = {"transformers": "transformer", "cables": "cable", "capacitor_banks": "capacitor_bank",
         "shunt_reactors": "shunt_reactor", "statcoms": "statcom", "switchgear": "switchgear"}
URLS = {"EI-NVL": "https://ei.se/download/18.61d7efeb18b048db98c4e48/1697184391450/Normv%C3%A4rdeslista-2024-2027.xlsx",
        "DEA-TRANS": "https://ens.dk/media/7680/download", "DEA-DIST": "https://ens.dk/media/6440/download"}
YEARS = {"EI-NVL": 2023, "DEA-TRANS": 2025, "DEA-DIST": 2025}
CODE = re.compile(r"\b(?:[RL]-N[RG]-[A-Z]+-\d+-\d+|NG\d+)\b")

costs = {}
for listing, kind in KINDS.items():
    for e in prop[listing]:
        capex = e["capex_eur_per_km"] if kind == "cable" else e["capex_eur"]
        costs[f"{kind}.{e['id'].lower()}"] = {"overnight": capex, "lifetime": e["lifetime_a"], "fom_share": e["opex_frac"]}

def provenance(parameter, d):
    note = d.get("note") or ""
    if d["source"] == "assumed":
        return "assumed (gridspine campus_assets.yaml)", "", 2026, "true"
    assert d["source"] == "literature", d
    key = note.split("]", 1)[0].lstrip("[")
    if key == "EI-NVL":
        codes = list(dict.fromkeys(CODE.findall(note)))
        assert codes, note
        src = f"Ei Normvärdeslista elnät 2024-2027 ({', '.join(codes)}), converted"
    elif key == "DEA-TRANS":
        src = "Danish Energy Agency, Technology Data for Energy Transport v0008 (Table 13)"
    elif key == "DEA-DIST":
        where = "note C" if parameter == "lifetime" else "notes L and Q"
        src = f"Danish Energy Agency, Technology Data for Energy Transport v0006, ch. 111 ({where})"
    else:
        raise SystemExit(f"unknown key {key}")
    return src, URLS[key], YEARS[key], "false"

dst = V / NEW
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(V / PREV, dst)
with (dst / "values.csv").open(encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh)); header = list(rows[0])
n = 0
for r in rows:
    c = costs.get(r["technology"])
    if c is None:
        continue
    d = c[r["parameter"]]
    v = float(d["value"])
    src, url, year, illus = provenance(r["parameter"], d)
    r.update(original_value=repr(v), source=src, source_url=url, source_year=str(year),
             illustrative=illus, note=d.get("note") or "")
    if r["money_year"]:
        r["money_year"] = "2025"
    n += 1
assert n == 243, n
with (dst / "values.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n"); w.writeheader(); w.writerows(rows)
m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
m["version"] = NEW
ce = m["seeded_from"]["campus_equipment"]
ce["sha256"] = hashlib.sha256(shipped.read_bytes()).hexdigest()
ce["price_year"] = 2025
ce["reference"] = ("docs/superpowers/research/2026-10-12-campus-asset-reference-costs.md (cited reference costs: "
                   "Ei Normvärdeslista elnät 2024-2027, Danish Energy Agency Technology Data for Energy Transport; "
                   "2022 SEK to 2025 EUR at the ECB rate and the euro-area C27.1 producer price index); "
                   "a value without a source stays assumed (illustrative)")
m["campus_reference_costs"] = {
    "from": PREV,
    "why": ("campus-IC 03A: the 243 campus equipment rows (overnight, lifetime, fom_share of 81 entries) take "
            "the cited reference costs in 2025 EUR; every other row, the finance defaults, tariffs and load "
            "profiles are 2026-10-22's unchanged"),
}
(dst / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", ce["sha256"])
