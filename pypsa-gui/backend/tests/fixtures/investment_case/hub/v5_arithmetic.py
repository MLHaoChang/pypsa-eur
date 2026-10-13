"""V5 — the energy-hub allocation oracle (IC P3 WP3.3a; plan § Oracles V5).

Three members on one group connection, 15-min metering, 7 days (2029-01-01 to
2029-01-07, a naive clock), self-authored. The arithmetic with the stdlib only:
it never imports the tariff engine or the ledger, which the fixture tests. It
writes `v5_energy_hub.json` with the working in `_working` and the expected
member shares (site view: + the member pays the hub) of every shared source
under each of the four allocation keys, to the cent.

    python v5_arithmetic.py <out_dir>

`test_value_flow_allocation.py` regenerates the file and requires it unchanged.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])

DAYS, STEP_H = 7, 0.25
PROFILE = {
    "start": "2029-01-01T00:00", "days": DAYS, "step_min": 15,
    "members": {
        "member_a": {"link": "la", "base": 1.0, "spike": {"at": "2029-01-03T12:00", "mw": 4.0}},
        "member_b": {"link": "lb", "base": 0.5, "window": {"from": 6, "to": 18, "mw": 2.0}},
        "member_c": {"link": "lc", "base": 0.5},
    },
    "export": {"window": {"from": 10, "to": 14, "mw": 0.3}},
}
TARIFF = {"id": "v5", "name": "V5 hub tariff", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [
              {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
                  {"name": "night", "rate": 0.06, "start_hour": 0, "end_hour": 6},
                  {"name": "day", "rate": 0.18}]},
              {"id": "levy", "kind": "tax_levy", "unit": "per_kwh",
               "periods": [{"name": "all", "rate": 0.02}]},
              {"id": "demand", "kind": "demand", "unit": "per_kw_month",
               "periods": [{"name": "all", "rate": 9.0}]},
              {"id": "standing", "kind": "fixed", "unit": "per_month",
               "periods": [{"name": "all", "rate": 150.0}]},
              {"id": "feed_in", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
               "direction": "revenue", "periods": [{"name": "all", "rate": 0.01}]}]}
CONNECTION_FEE, EXPORT_PRICE_REVENUE = 1000.0, 500.0
KEYS = {"contracted_capacity": {"member_a": 5.0, "member_b": 3.0, "member_c": 2.0},
        "fixed_shares": {"member_a": 0.25, "member_b": 0.25, "member_c": 0.5}}
MEMBERS = sorted(PROFILE["members"])            # member_c (last) takes the remainder


def c(x):
    return round(x + 0.0, 2)


# ── per-member energy (kWh) by rate window ─────────────────────────────────
night_h = 6 * DAYS                     # 00–06 at 0.06
day_h = 18 * DAYS                      # 06–24 at 0.18
kwh = {
    # A: 1 MW flat; one 15-min interval at 4 MW (2029-01-03 12:00, day rate): +3 MW × 0.25 h
    "member_a": {"night": 1000 * night_h, "day": 1000 * day_h + 3000 * STEP_H},
    # B: 2 MW 06–18 (day), 0.5 MW otherwise (00–06 night, 18–24 day)
    "member_b": {"night": 500 * night_h, "day": 2000 * 12 * DAYS + 500 * 6 * DAYS},
    "member_c": {"night": 500 * night_h, "day": 500 * day_h},
}
metered = {m: {"energy": 0.06 * k["night"] + 0.18 * k["day"],
               "levy": 0.02 * (k["night"] + k["day"])} for m, k in kwh.items()}
energy_mwh = {m: (k["night"] + k["day"]) / 1000 for m, k in kwh.items()}

# ── the group bill ─────────────────────────────────────────────────────────
# Group peak: the spike interval, 4 + 2 + 0.5 = 6.5 MW = 6500 kW (every other
# daytime interval is 3.5 MW); one (partial) month billed at the full rate.
peak_kw = {"member_a": 4000.0, "member_b": 2000.0, "member_c": 500.0}
group_peak = sum(peak_kw.values())
export_kwh = 300 * 4 * DAYS                            # 0.3 MW, 10–14 h
bill = {
    "energy": sum(v["energy"] for v in metered.values()),
    "levy": sum(v["levy"] for v in metered.values()),
    "demand": 9.0 * group_peak,
    "standing": 150.0 * (24 * DAYS) / (24 * 31),       # covered share of January
    "feed_in": -0.01 * export_kwh,
}
shared = {"bill:energy": bill["energy"], "bill:levy": bill["levy"],
          "bill:demand": bill["demand"], "bill:standing": bill["standing"],
          "bill:feed_in": bill["feed_in"], "connection:fee": CONNECTION_FEE,
          "export_price:export_price": -EXPORT_PRICE_REVENUE}


def split(amount, weights):
    tot = sum(weights.values())
    raw = {m: amount * weights[m] / tot for m in MEMBERS}
    out = {m: raw[m] for m in MEMBERS[:-1]}
    out[MEMBERS[-1]] = amount - sum(out.values())
    return out


def metered_split(item):
    got = {m: metered[m][item] for m in MEMBERS}
    out = {m: got[m] for m in MEMBERS[:-1]}
    out[MEMBERS[-1]] = bill[item] - sum(out.values())
    return out


expected = {}
for key in ("contracted_capacity", "fixed_shares", "energy", "peak_contribution"):
    rows = {}
    for sid, amount in shared.items():
        if sid in ("bill:energy", "bill:levy"):
            rows[sid] = metered_split(sid.split(":")[1])
        elif key == "peak_contribution" and sid == "bill:demand":
            rows[sid] = split(amount, peak_kw)
        elif key in KEYS:
            rows[sid] = split(amount, KEYS[key])
        else:                                   # energy, and peak's fallback for the rest
            rows[sid] = split(amount, energy_mwh)
    for sid, r in rows.items():
        for m, v in r.items():
            # No expected value on a half-cent tie: rounding could go either way.
            assert abs(abs(v * 100) % 1 - 0.5) > 1e-6, (key, sid, m, v)
    expected[key] = {sid: {m: c(v) for m, v in r.items()} for sid, r in rows.items()}

working = [
    "V5 — an energy hub: three members behind one group connection, 15-min, 2029-01-01..07 (672 rows), naive clock. Self-authored; illustrative prices.",
    f"Energy TOU: night 00–06 at 0.06 €/kWh ({night_h} h), day otherwise at 0.18 €/kWh ({day_h} h). Levy 0.02 €/kWh on import.",
    "member_a: 1 MW flat, 4 MW in the interval 2029-01-03 12:00 (+750 kWh at the day rate) → "
    f"energy {c(metered['member_a']['energy'])} €, levy {c(metered['member_a']['levy'])} €, {energy_mwh['member_a']} MWh.",
    "member_b: 2 MW 06–18, 0.5 MW otherwise → "
    f"energy {c(metered['member_b']['energy'])} €, levy {c(metered['member_b']['levy'])} €, {energy_mwh['member_b']} MWh.",
    "member_c: 0.5 MW flat → "
    f"energy {c(metered['member_c']['energy'])} €, levy {c(metered['member_c']['levy'])} €, {energy_mwh['member_c']} MWh.",
    "Linear import items (energy, levy) are metered per member; the last member (member_c) takes the remainder of the group item.",
    f"Group peak: the spike interval, 4 + 2 + 0.5 MW = {group_peak:g} kW; demand 9 €/kW-month → {c(bill['demand'])} €. "
    "peak_contribution splits it 4 : 2 : 0.5.",
    f"Standing charge 150 €/month × 168 h / 744 h = {c(bill['standing'])} €.",
    f"Export 0.3 MW 10–14 h → {export_kwh} kWh; feed-in 0.01 €/kWh → revenue {c(-bill['feed_in'])} € (site view negative: the hub pays each member its share).",
    f"Connection fee {CONNECTION_FEE} €; export-price revenue {EXPORT_PRICE_REVENUE} € (site view −).",
    "Keys: contracted_capacity 5 : 3 : 2 MW; fixed_shares 0.25 : 0.25 : 0.5; energy by MWh "
    f"{energy_mwh['member_a']} : {energy_mwh['member_b']} : {energy_mwh['member_c']}; peak_contribution "
    "splits demand items by the billed interval and falls back to energy for the rest.",
]
(OUT / "v5_energy_hub.json").write_text(json.dumps({
    "_working": working, "profile": PROFILE, "tariff": TARIFF,
    "connection_fee": CONNECTION_FEE, "export_price_revenue": EXPORT_PRICE_REVENUE,
    "keys": KEYS, "bill": {k: c(v) for k, v in bill.items()},
    "metered": {m: {k: c(v) for k, v in d.items()} for m, d in metered.items()},
    "energy_mwh": energy_mwh, "expected": expected}, indent=1, ensure_ascii=False) + "\n")
