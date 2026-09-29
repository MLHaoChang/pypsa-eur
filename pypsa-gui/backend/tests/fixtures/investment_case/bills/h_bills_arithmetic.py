"""The H1-H3 hand-rated bills (IC P2 oracles; plan § Oracles).

The arithmetic of each bill, with the stdlib only (calendar, datetime, zoneinfo):
it never imports the tariff engine, which the fixtures test. It writes
`h1_de_rlm.json`, `h2_nl_business.json` and `h3_us_ci.json` with the working in
each file's `_working`, and checks the invariants the working relies on (every
NL month in the same energy-tax band, the windowed-tier blend equal to the
per-tier split, no expected value on a half-cent tie).

    python h_bills_arithmetic.py <out_dir>

`test_tariff_engine_core.py` regenerates the files and requires them unchanged.
"""
import calendar
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(sys.argv[1])


def month_hours(y, m, tz):
    z = ZoneInfo(tz)
    a = dt.datetime(y, m, 1, tzinfo=z)
    b = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=z)
    return (b.astimezone(dt.timezone.utc) - a.astimezone(dt.timezone.utc)).total_seconds() / 3600


def weekdays(y, m):
    return sum(1 for d in range(1, calendar.monthrange(y, m)[1] + 1)
               if dt.date(y, m, d).weekday() < 5)


def c(x):
    return round(x + 0.0, 2)


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


MONTHS = [f"2030-{m:02d}" for m in range(1, 13)]

# ───────────────────────────── H1 (DE) ─────────────────────────────
tz = "Europe/Berlin"
h = {k: month_hours(2030, m, tz) for m, k in enumerate(MONTHS, 1)}
base_kw = 400.0
extra = {"2030-02": (1000 - 400) * 1.0, "2030-07": (1200 - 400) * 0.25}
e = {k: base_kw * h[k] + extra.get(k, 0.0) for k in MONTHS}
V = sum(e.values())
peak = 1200.0
AP, LP, STS, KA, MSB = 0.0291, 98.52, 0.0205, 0.0011, 45.00
cap_m = {k: LP * peak * h[k] / 8760 for k in MONTHS}
h1_items = {"arbeitspreis": V * AP, "leistungspreis": LP * peak, "stromsteuer": V * STS,
            "konzessionsabgabe": V * KA, "messstellenbetrieb": 12 * MSB}
assert abs(sum(cap_m.values()) - LP * peak) < 1e-6
work = [
    "H1 — Germany, industrial customer on the medium-voltage grid, calendar year 2030, Europe/Berlin, 15-min metering (RLM). Self-authored; illustrative prices, not a published price sheet.",
    "Load: 400 kW flat all year, plus 1,000 kW for one hour on Tue 2030-02-05 08:00-09:00 and 1,200 kW for one quarter-hour on Wed 2030-07-10 11:00-11:15 (local).",
    "Local month hours (DST): March 743 h (spring forward 2030-03-31), October 745 h (fall back 2030-10-27), the year 8,760 h; the 15-min axis has 35,040 intervals.",
    f"Energy V = 400 x 8,760 + (1,000-400) x 1 h + (1,200-400) x 0.25 h = 3,504,000 + 600 + 200 = {V:,.0f} kWh.",
    f"Annual 15-min peak (Jahreshöchstleistung) = 1,200 kW. Benutzungsdauer = V / peak = {V/peak:,.2f} h >= 2,500 h, so the >= 2,500 h price pair applies: Leistungspreis {LP} EUR/kW-a, Arbeitspreis {AP} EUR/kWh (the < 2,500 h pair is 18.20 EUR/kW-a and 0.0612 EUR/kWh, not used). The engine does not choose the pair; the tariff states the applicable one.",
    f"arbeitspreis = {V:,.0f} x {AP} = {h1_items['arbeitspreis']:,.4f} -> {c(h1_items['arbeitspreis']):,.2f}",
    f"leistungspreis = {LP} x 1,200 = {h1_items['leistungspreis']:,.2f}; billed once on the annual peak and spread over the months by hours / 8,760: February {LP} x 1,200 x 672 / 8,760 = {cap_m['2030-02']:,.4f}, March x 743 / 8,760 = {cap_m['2030-03']:,.4f}, October x 745 / 8,760 = {cap_m['2030-10']:,.4f}",
    f"stromsteuer = {V:,.0f} x {STS} = {h1_items['stromsteuer']:,.2f}",
    f"konzessionsabgabe = {V:,.0f} x {KA} = {h1_items['konzessionsabgabe']:,.4f} -> {c(h1_items['konzessionsabgabe']):,.2f}",
    f"messstellenbetrieb = 12 x {MSB:.2f} = {h1_items['messstellenbetrieb']:,.2f}",
    f"total = {sum(h1_items.values()):,.4f} -> {c(sum(h1_items.values())):,.2f}",
    "Documented non-support: §19 Abs. 2 StromNEV individual network charges (atypical use or >= 7,000 h and >= 10 GWh) are a regulator agreement per customer, not a tariff item; they are not modelled, and a tariff that needs them must name them in `unsupported_fields` (the bill then has no total). The §19 StromNEV surcharge on consumption (now the Aufschlag für besondere Netznutzung) is an ordinary per-kWh levy and could be one more tax_levy item.",
]
h1 = {
    "_working": work,
    "timezone": tz,
    "tariff": {"id": "h1_de_rlm", "name": "H1 DE medium voltage RLM", "jurisdiction": "DE",
               "valid_from": "2030-01-01", "items": [
                   {"id": "arbeitspreis", "kind": "energy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": AP}], "measured_on": "import"},
                   {"id": "leistungspreis", "kind": "capacity", "unit": "per_kw_year",
                    "periods": [{"name": "all", "rate": LP}], "measured_on": "peak_import"},
                   {"id": "stromsteuer", "kind": "tax_levy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": STS}], "measured_on": "import"},
                   {"id": "konzessionsabgabe", "kind": "tax_levy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": KA}], "measured_on": "import"},
                   {"id": "messstellenbetrieb", "kind": "fixed", "unit": "per_month",
                    "periods": [{"name": "all", "rate": MSB}]}]},
    "dispatch_range": {"start": "2030-01-01 00:00", "end": "2030-12-31 23:45", "freq": "15min",
                       "import_mw": 0.4, "export_mw": 0.0,
                       "overrides": [
                           {"start": "2030-02-05 08:00", "end": "2030-02-05 09:00", "import_mw": 1.0},
                           {"start": "2030-07-10 11:00", "end": "2030-07-10 11:15", "import_mw": 1.2}]},
    "step_hours": 0.25,
    "expected": {
        "per_item": {k: c(v) for k, v in h1_items.items()},
        "monthly": {
            "2030-02": {"leistungspreis": c(cap_m["2030-02"]), "arbeitspreis": c(e["2030-02"] * AP),
                        "messstellenbetrieb": MSB},
            "2030-03": {"leistungspreis": c(cap_m["2030-03"]), "arbeitspreis": c(e["2030-03"] * AP)},
            "2030-07": {"arbeitspreis": c(e["2030-07"] * AP)},
            "2030-10": {"leistungspreis": c(cap_m["2030-10"]), "arbeitspreis": c(e["2030-10"] * AP)}},
        "total": c(sum(h1_items.values()))},
}
work.insert(-2, f"monthly arbeitspreis: February (400 x 672 + 600) x {AP} = {e['2030-02']*AP:,.4f}; March 400 x 743 x {AP} = {e['2030-03']*AP:,.4f}; July (400 x 744 + 200) x {AP} = {e['2030-07']*AP:,.4f}; October 400 x 745 x {AP} = {e['2030-10']*AP:,.4f}")
dump("h1_de_rlm.json", h1)

# ───────────────────────────── H2 (NL) ─────────────────────────────
tz = "Europe/Amsterdam"
h = {k: month_hours(2030, m, tz) for m, k in enumerate(MONTHS, 1)}
wd = {k: weekdays(2030, m) for m, k in enumerate(MONTHS, 1)}
base_kw = 200.0
spike = {"2030-07": (500 - 200) * 0.25, "2030-12": (450 - 200) * 0.25}   # both in normaal
normaal = {k: base_kw * wd[k] * 16 + spike.get(k, 0.0) for k in MONTHS}
dal = {k: base_kw * (h[k] - wd[k] * 16) for k in MONTHS}
vol = {k: normaal[k] + dal[k] for k in MONTHS}
V = sum(vol.values())
R_N, R_D = 0.1200, 0.0900
EB = [0.10154, 0.06937, 0.03868, 0.00321]
T_ANNUAL = [0.0, 10_000.0, 50_000.0, 10_000_000.0]
T = [t / 12 for t in T_ANNUAL]
GVO, VAST, HK, KWMAX = 0.0021, 0.9500, 1.7400, 3.50
peak = {k: 200.0 for k in MONTHS}
peak["2030-07"], peak["2030-12"] = 500.0, 450.0


def eb_month(v):
    th = T + [float("inf")]
    return sum(r * min(max(v - th[i], 0.0), th[i + 1] - th[i]) for i, r in enumerate(EB))


assert all(T[2] <= v <= T[3] for v in vol.values())      # every month in band 3
eb_statutory = EB[0] * 10_000 + EB[1] * 40_000 + EB[2] * (V - 50_000)
eb_monthly = sum(eb_month(v) for v in vol.values())
assert abs(eb_statutory - eb_monthly) < 1e-6
days = {k: calendar.monthrange(2030, m)[1] for m, k in enumerate(MONTHS, 1)}
h2_items = {"levering": sum(normaal.values()) * R_N + sum(dal.values()) * R_D,
            "energiebelasting": eb_statutory, "gvo": V * GVO, "vastrecht": 365 * VAST,
            "heffingskorting": -365 * HK, "kw_max": sum(peak[k] * KWMAX for k in MONTHS)}
work = [
    "H2 — Netherlands, business connection (kleinverbruik-sized volume on a grootverbruik meter), calendar year 2030, Europe/Amsterdam, 15-min metering. Self-authored; rates are illustrative (the energy-tax rates are of the order of the 2025 bands), not a published rate card.",
    "Load: 200 kW flat, plus 500 kW on Wed 2030-07-10 10:00-10:15 and 450 kW on Wed 2030-12-11 19:00-19:15 (local).",
    "levering (supply) TOU: normaal Mon-Fri 07:00-23:00 at 0.1200 EUR/kWh, dal otherwise at 0.0900. 2030 has 261 weekdays (Jan 23, Feb 20, Mar 21, Apr 22, May 23, Jun 20, Jul 23, Aug 22, Sep 21, Oct 23, Nov 21, Dec 22); both DST switches (2030-03-31, 2030-10-27) are Sundays, so the missing and repeated hours are dal and normaal hours = 261 x 16 = 4,176 h.",
    f"normaal kWh = 200 x 4,176 + 300 x 0.25 + 250 x 0.25 = 835,200 + 75 + 62.5 = {sum(normaal.values()):,.1f}; dal kWh = 200 x (8,760 - 4,176) = {sum(dal.values()):,.1f}; V = {V:,.1f} kWh",
    f"levering = {sum(normaal.values()):,.1f} x 0.12 + {sum(dal.values()):,.1f} x 0.09 = {h2_items['levering']:,.4f} -> {c(h2_items['levering']):,.2f}",
    "energiebelasting (energy tax) bands are ANNUAL per connection: 0-10,000 kWh, 10,000-50,000, 50,000-10,000,000, above. The engine's tiers reset MONTHLY, so the tariff carries the bands as monthly thresholds (annual / 12: 833.33, 4,166.67, 833,333.33 kWh). The two agree when every month's volume lies in the same band position (here every month is between 4,166.67 and 833,333.33 kWh — the smallest is February, 200 x 672 = 134,400 kWh): each band then holds the same annual volume. The monthly distribution differs (the statutory bill front-loads band 1 into January); the annual total does not.",
    f"energiebelasting (statutory, annual) = 10,000 x {EB[0]} + 40,000 x {EB[1]} + ({V:,.1f} - 50,000) x {EB[2]} = {eb_statutory:,.4f} -> {c(eb_statutory):,.2f}",
    f"energiebelasting in January (monthly thresholds) = 833.33 x {EB[0]} + 3,333.33 x {EB[1]} + ({vol['2030-01']:,.0f} - 4,166.67) x {EB[2]} = {eb_month(vol['2030-01']):,.4f}",
    f"gvo (guarantees of origin, certificate) = {V:,.1f} x {GVO} = {h2_items['gvo']:,.4f} -> {c(h2_items['gvo']):,.2f}",
    f"vastrecht (transport standing charge) = 365 days x {VAST} = {h2_items['vastrecht']:,.2f}; heffingskorting (energy-tax credit, per connection) = -365 x {HK} = {h2_items['heffingskorting']:,.2f} (revenue: negative from the site's view)",
    f"kw_max (monthly 15-min peak) = 3.50 x (10 x 200 + 500 + 450) = {h2_items['kw_max']:,.2f}",
    f"total = {sum(h2_items.values()):,.4f} -> {c(sum(h2_items.values())):,.2f}",
]
h2 = {
    "_working": work,
    "timezone": tz,
    "tariff": {"id": "h2_nl_business", "name": "H2 NL business connection", "jurisdiction": "NL",
               "valid_from": "2030-01-01", "items": [
                   {"id": "levering", "kind": "energy", "unit": "per_kwh", "periods": [
                       {"name": "normaal", "rate": R_N, "weekdays": [0, 1, 2, 3, 4],
                        "start_hour": 7, "end_hour": 23},
                       {"name": "dal", "rate": R_D}]},
                   {"id": "energiebelasting", "kind": "tax_levy", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": 0.0}],
                    "tiers": [{"threshold": t, "rate": r} for t, r in zip(T, EB)]},
                   {"id": "gvo", "kind": "certificate", "unit": "per_kwh",
                    "periods": [{"name": "all", "rate": GVO}]},
                   {"id": "vastrecht", "kind": "fixed", "unit": "per_day",
                    "periods": [{"name": "all", "rate": VAST}]},
                   {"id": "heffingskorting", "kind": "fixed", "unit": "per_day",
                    "periods": [{"name": "all", "rate": HK}], "direction": "revenue"},
                   {"id": "kw_max", "kind": "demand", "unit": "per_kw_month",
                    "periods": [{"name": "all", "rate": KWMAX}]}]},
    "dispatch_range": {"start": "2030-01-01 00:00", "end": "2030-12-31 23:45", "freq": "15min",
                       "import_mw": 0.2, "export_mw": 0.0,
                       "overrides": [
                           {"start": "2030-07-10 10:00", "end": "2030-07-10 10:15", "import_mw": 0.5},
                           {"start": "2030-12-11 19:00", "end": "2030-12-11 19:15", "import_mw": 0.45}]},
    "step_hours": 0.25,
    "expected": {
        "per_item": {k: c(v) for k, v in h2_items.items()},
        "monthly": {
            "2030-01": {"energiebelasting": c(eb_month(vol["2030-01"])),
                        "levering": c(normaal["2030-01"] * R_N + dal["2030-01"] * R_D),
                        "vastrecht": c(31 * VAST), "heffingskorting": c(-31 * HK), "kw_max": 700.0},
            "2030-03": {"levering": c(normaal["2030-03"] * R_N + dal["2030-03"] * R_D),
                        "vastrecht": c(31 * VAST)},
            "2030-07": {"kw_max": 1750.0, "levering": c(normaal["2030-07"] * R_N + dal["2030-07"] * R_D)},
            "2030-10": {"levering": c(normaal["2030-10"] * R_N + dal["2030-10"] * R_D)},
            "2030-12": {"kw_max": 1575.0}},
        "total": c(sum(h2_items.values()))},
}
work.insert(-1, "monthly levering: (normaal kWh) x 0.12 + (dal kWh) x 0.09 with normaal = 200 x weekdays x 16 (+ the spike), dal = 200 x (month hours - weekdays x 16); March 743 h: 200 x 336 = 67,200 normaal, 200 x 407 = 81,400 dal; October 745 h: 200 x 368 = 73,600 normaal, 200 x 377 = 75,400 dal")
dump("h2_nl_business.json", h2)

# ───────────────────────────── H3 (US C&I) ─────────────────────────────
tz = "America/New_York"
M3 = ["2030-06", "2030-07", "2030-08"]
h = {k: month_hours(2030, int(k[5:]), tz) for k in M3}
wd = {k: weekdays(2030, int(k[5:])) for k in M3}
base = 300.0
# (month, kWh extra, in energy 'peak' window?)
spikes = [("2030-06", (800 - 300) * 0.25, False),   # Tue 06-04 03:00  off
          ("2030-06", (650 - 300) * 0.25, False),   # Wed 06-05 09:00  off energy; on_peak demand frag 1
          ("2030-06", (700 - 300) * 0.25, True),    # Wed 06-05 18:00  peak energy; on_peak demand frag 2
          ("2030-06", (550 - 300) * 0.25, True),    # Thu 06-06 14:00  peak energy; mid_peak demand
          ("2030-07", (750 - 300) * 0.25, False)]   # Tue 07-09 10:00  off energy; on_peak demand frag 1
E_pk = {k: base * wd[k] * 8 + sum(x for m, x, p in spikes if m == k and p) for k in M3}
E_off = {k: base * (h[k] - wd[k] * 8) + sum(x for m, x, p in spikes if m == k and not p) for k in M3}
E = {k: E_pk[k] + E_off[k] for k in M3}
TH = [0.0, 100_000.0, 200_000.0]
R_PK, R_OFF = [0.20, 0.23, 0.26], [0.08, 0.10, 0.12]


def widths(total):
    return [100_000.0, 100_000.0, total - 200_000.0]


assert all(v > 200_000 for v in E.values())
blend = {k: (sum(r * w for r, w in zip(R_PK, widths(E[k]))) / E[k],
             sum(r * w for r, w in zip(R_OFF, widths(E[k]))) / E[k]) for k in M3}
energy_m = {k: E_pk[k] * blend[k][0] + E_off[k] * blend[k][1] for k in M3}
# the same month cost without blending: each tier's volume at the period-weighted rate
for k in M3:
    s = E_pk[k] / E[k]
    alt = sum(w * (s * rp + (1 - s) * ro) for w, rp, ro in zip(widths(E[k]), R_PK, R_OFF))
    assert abs(alt - energy_m[k]) < 1e-6
FAC = 9.00
hist = {"2029-07": 500.0, "2029-08": 500.0, "2029-09": 900.0, "2029-10": 500.0, "2029-11": 500.0,
        "2029-12": 500.0, "2030-01": 500.0, "2030-02": 500.0, "2030-03": 500.0, "2030-04": 500.0,
        "2030-05": 500.0}
fac_actual = {"2030-06": 800.0, "2030-07": 750.0, "2030-08": 300.0}
fac_billed = {"2030-06": max(800.0, 0.8 * 900), "2030-07": max(750.0, 0.8 * 900),
              "2030-08": max(300.0, 0.8 * 900)}
ONP, MIDP = 12.00, 4.00
on_peak = {"2030-06": 700.0, "2030-07": 750.0, "2030-08": 300.0}
mid_peak = {"2030-06": 550.0, "2030-07": 300.0, "2030-08": 300.0}
CUST = 9.8630
h3_items = {"energy": sum(energy_m.values()),
            "facility": sum(FAC * v for v in fac_billed.values()),
            "tou_demand": sum(ONP * on_peak[k] + MIDP * mid_peak[k] for k in M3),
            "customer": 92 * CUST}
work = [
    "H3 — US commercial & industrial, summer quarter 2030-06-01 to 2030-08-31, America/New_York (EDT throughout), 15-min interval data. Self-authored; illustrative rates in the shape of a URDB tariff, not a published schedule. No holiday calendar: July 4 (a Thursday) is a weekday here, as in URDB schedules.",
    "Load: 300 kW flat, plus one quarter-hour each at 800 kW Tue 06-04 03:00, 650 kW Wed 06-05 09:00, 700 kW Wed 06-05 18:00, 550 kW Thu 06-06 14:00, 750 kW Tue 07-09 10:00 (local).",
    "energy — a 3-tier windowed tariff with URDB semantics: the month's TOTAL kWh positions the tiers (0-100,000, 100,000-200,000, above; cumulative thresholds), and each period's kWh is split into the tiers in proportion to the total. Periods: peak Mon-Fri 12:00-20:00 (Jun-Sep) at 0.20 / 0.23 / 0.26 USD/kWh by tier, off (everything else) at 0.08 / 0.10 / 0.12.",
    f"weekdays: June {wd['2030-06']}, July {wd['2030-07']}, August {wd['2030-08']}; peak kWh = 300 x weekdays x 8 (+ the 18:00 and 14:00 June spikes, 100 + 62.5): June {E_pk['2030-06']:,.1f}, July {E_pk['2030-07']:,.1f}, August {E_pk['2030-08']:,.1f}; off kWh = 300 x (hours - weekdays x 8) (+ the 03:00, 09:00 and July 10:00 spikes, 125 + 87.5 and 112.5): June {E_off['2030-06']:,.1f}, July {E_off['2030-07']:,.1f}, August {E_off['2030-08']:,.1f}",
    *[f"energy {k}: E = {E[k]:,.1f}; tier volumes 100,000 / 100,000 / {E[k]-200_000:,.1f}; share of peak s = {E_pk[k]:,.1f} / {E[k]:,.1f}; cost = sum over tiers of volume x (s x peak rate + (1 - s) x off rate) = {energy_m[k]:,.4f}" for k in M3],
    f"energy = {h3_items['energy']:,.4f} -> {c(h3_items['energy']):,.2f}",
    "facility — all-hours monthly 15-min peak at 9.00 USD/kW with an 11-month ratchet at 80 % of the highest ACTUAL peak of the 11 months before (meter history seeds July 2029-May 2030: 500 kW, except September 2029 at 900 kW).",
    "June: actual 800, floor 0.8 x 900 = 720 -> 800; July: actual 750, floor 0.8 x max(900, June 800) = 720 -> 750; August: actual 300, floor 720 -> 720.",
    f"facility = 9.00 x (800 + 750 + 720) = {h3_items['facility']:,.2f}",
    "tou_demand — windowed demand with a SPLIT-PEAK period: on_peak is ONE window of two fragments, Mon-Fri 08:00-12:00 and 17:00-21:00 (Jun-Sep), at 12.00 USD/kW on the single monthly peak across both fragments; mid_peak Mon-Fri 12:00-17:00 at 4.00 USD/kW. Hours outside both windows are not measured.",
    "on_peak: June max(650 at 09:00, 700 at 18:00) = 700 (one peak, not 650 + 700); July 750; August 300. mid_peak: June 550; July 300; August 300.",
    f"tou_demand = 12 x (700 + 750 + 300) + 4 x (550 + 300 + 300) = 21,000 + 4,600 = {h3_items['tou_demand']:,.2f}",
    f"customer — fixed 9.8630 USD/day: 92 days = {h3_items['customer']:,.4f} -> {c(h3_items['customer']):,.2f}; June 30 x 9.863 = 295.89, July and August 31 x 9.863 = 305.753 -> 305.75",
    f"total = {sum(h3_items.values()):,.4f} -> {c(sum(h3_items.values())):,.2f}",
]
h3 = {
    "_working": work,
    "timezone": tz,
    "tariff": {"id": "h3_us_ci", "name": "H3 US C&I summer", "jurisdiction": "US",
               "valid_from": "2030-01-01", "items": [
                   {"id": "energy", "kind": "energy", "unit": "per_kwh",
                    "tiers": [{"threshold": t, "rate": 0.0} for t in TH], "periods": [
                        {"name": "peak", "rate": 0.0, "months": [6, 7, 8, 9],
                         "weekdays": [0, 1, 2, 3, 4], "start_hour": 12, "end_hour": 20,
                         "tier_rates": R_PK},
                        {"name": "off", "rate": 0.0, "tier_rates": R_OFF}]},
                   {"id": "facility", "kind": "demand", "unit": "per_kw_month",
                    "periods": [{"name": "all", "rate": FAC}],
                    "ratchet": {"lookback_months": 11, "share": 0.8}},
                   {"id": "tou_demand", "kind": "demand", "unit": "per_kw_month", "periods": [
                       {"name": "on_peak", "rate": ONP, "months": [6, 7, 8, 9],
                        "weekdays": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 12},
                       {"name": "mid_peak", "rate": MIDP, "months": [6, 7, 8, 9],
                        "weekdays": [0, 1, 2, 3, 4], "start_hour": 12, "end_hour": 17},
                       {"name": "on_peak", "rate": ONP, "months": [6, 7, 8, 9],
                        "weekdays": [0, 1, 2, 3, 4], "start_hour": 17, "end_hour": 21}]},
                   {"id": "customer", "kind": "fixed", "unit": "per_day",
                    "periods": [{"name": "all", "rate": CUST}]}]},
    "dispatch_range": {"start": "2030-06-01 00:00", "end": "2030-08-31 23:45", "freq": "15min",
                       "import_mw": 0.3, "export_mw": 0.0,
                       "overrides": [
                           {"start": "2030-06-04 03:00", "end": "2030-06-04 03:15", "import_mw": 0.8},
                           {"start": "2030-06-05 09:00", "end": "2030-06-05 09:15", "import_mw": 0.65},
                           {"start": "2030-06-05 18:00", "end": "2030-06-05 18:15", "import_mw": 0.7},
                           {"start": "2030-06-06 14:00", "end": "2030-06-06 14:15", "import_mw": 0.55},
                           {"start": "2030-07-09 10:00", "end": "2030-07-09 10:15", "import_mw": 0.75}]},
    "meter_history": hist,
    "step_hours": 0.25,
    "expected": {
        "per_item": {k: c(v) for k, v in h3_items.items()},
        "monthly": {k: {"energy": c(energy_m[k]), "facility": c(FAC * fac_billed[k]),
                        "tou_demand": c(ONP * on_peak[k] + MIDP * mid_peak[k]),
                        "customer": c(CUST * (30 if k == "2030-06" else 31))} for k in M3},
        "demand_billed_kw": {"facility": fac_billed, "tou_demand:on_peak": on_peak,
                             "tou_demand:mid_peak": mid_peak},
        "total": c(sum(h3_items.values()))},
}
dump("h3_us_ci.json", h3)
for name, items in (("H1", h1_items), ("H2", h2_items), ("H3", h3_items)):
    print(name, {k: c(v) for k, v in items.items()}, "total", c(sum(items.values())))

# No expected value may sit on a half-cent tie (rounding would then depend on
# the last float bit, not on the arithmetic).
for items in (h1_items, h2_items, h3_items):
    for v in items.values():
        frac = abs(v * 100) % 1
        assert abs(frac - 0.5) > 1e-4, v
