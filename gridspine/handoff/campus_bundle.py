"""The campus PSS/E handoff: one bundle per island hour (island plan I8, PSS/E path).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

The first-pass checks (I4, I5, I6) are screenings. The client-grade island
study runs in PSS/E or PowerFactory, and this bundle hands it the case. One
directory per hour, standing alone:

* ``<case>.raw``: the hour, grid-connected, before the event (``apply_hour``
  on a copy of the campus net). The islanding event is the trip of the grid
  machine ``GRID`` at the PCC at t = 0, which the README names by bus number
  and machine ID;
* ``<case>.dyr``: for every genset with island data, a ``GENSAL`` (salient
  pole, the usual genset machine), a ``DEGOV1`` governor and, unless its
  ``excitation`` is ``none`` (constant field), a ``SEXS`` exciter; the grid
  machine is a ``GENCLS`` with H = 0, an infinite bus until it trips;
* ``gfm_models.csv``: the grid-forming converters' WECC parameters by name,
  ``REGFM_A1`` (droop) or ``REGFM_B1`` (VSM), in long form. They are not in
  the .dyr: those records' PSS/E CON order is not verified here, and a record
  in a guessed order imports cleanly as a wrong converter (owner decision);
* ``README.md``: the event, the records, every value drafted rather than
  given (with its source), what is omitted and why, and the ledger;
* ``manifest.json``.

**Identity** comes from the RAW writer: ``dyr_writer.machine_records`` gives
each machine's (bus number, machine ID) from the RAW's own counter, so the
.dyr and the CSV attach to the machines the .raw declares.

**Values.** A genset's H, x''d, x_d (its saturated ``xd_sat``) and droop come
from its island data; the rest of GENSAL, DEGOV1 and SEXS are typical values,
listed in the README as ``typical (assumed)``. Every gas engine is a DEGOV1
too (owner decision): a diesel model approximates it, ledgered. A GFM's
droops, P range and current limit come from its island data, converted from
its P_n to the inverter's S_n base; the rest are the WECC specifications'
example values (PNNL-35110 for REGFM_A1, the UNIFI VSM specification for
REGFM_B1).

**Omitted, and listed:** grid-following converters (REGCA1/REECA1 need the
OEM's 45 parameters; none is invented), UPSs, STATCOMs, and gensets without
island data.
"""
import copy
import dataclasses
import json
from pathlib import Path

import pandas as pd

from gridspine.handoff.dyr_writer import DYR_CONS, format_record, machine_records
from gridspine.handoff.raw_writer import write_raw
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_flow import apply_hour
from gridspine.templates.unit_params import _check_physics

TYPICAL = "typical (assumed)"
#: GENSAL values a campus genset does not state: a medium-speed salient-pole
#: alternator (assumed). X'd is raised above x''d where needed, X_l is half
#: of x''d and X_q a share of X_d, so the reactance ordering holds.
GENSAL_TYPICAL = {"d": 0.0, "xd_p": 0.25, "t_do_p": 2.0, "t_do_pp": 0.03, "t_qo_pp": 0.05, "s1": 0.1, "s12": 0.4}
XQ_SHARE, XL_SHARE, XDP_OVER_XDPP = 0.6, 0.5, 1.5
#: DEGOV1 values a campus genset does not state (assumed). M = 1: droop on
#: electrical power; K = 1 / DROOP; an isochronous governor has DROOP = 0
#: and K = 1 / ISO_DROOP.
DEGOV1_TYPICAL = {"m": 1, "t1": 0.2, "t2": 0.1, "t3": 0.5, "t4": 1.0, "t5": 0.1, "t6": 0.2, "td": 0.024,
                  "tmin": 0.0, "te": 0.1}
ISO_DROOP = 0.04
#: SEXS values (assumed); EMAX by the island data's ``excitation``.
SEXS_TYPICAL = {"ta_tb": 0.1, "tb": 10.0, "k": 50.0, "te": 0.05, "emin": 0.0}
SEXS_EMAX = {"avr_pmg": 4.0, "avr_shunt": 3.0}
#: The WECC example values for what the campus does not state.
REGFM_A1_EXAMPLE = {"XL": 0.15, "kpv": 0.0, "kiv": 5.86, "Emax": 1.15, "Emin": 0.0, "kppmax": 0.01, "kipmax": 0.1,
                    "TQf": 0.01, "TVf": 0.01, "Qmax": 0.44, "Qmin": -0.44, "VFlag": 1, "QVFlag": 1,
                    "kpqmax": 3.0, "Kiqmax": 20.0}
REGFM_B1_EXAMPLE = {"wFlag": 0, "VdrpFlag": 0, "QVFlag": 1, "PQFlag": 1, "FFlag": 1, "Re": 0.0, "XL": 0.1,
                    "kpv": 0.0, "kiv": 5.0, "dwmax": 0.05, "dwmin": -0.05, "kpPLL": 0.265, "kiPLL": 2.65,
                    "dwPLLmax": 0.2, "dwPLLmin": -0.2, "Tp": 0.0, "D1": 0.0, "D2": 100.0, "wD": 50.0,
                    "ImaxSS": 1.0, "kf": 0.9, "kI": 2.0, "TQf": 0.02, "TVf": 0.02, "TIf": 0.02, "Ke": 1.0}
SPEC = {"REGFM_A1": "PNNL-35110 example (assumed)", "REGFM_B1": "UNIFI VSM specification example (assumed)"}
PENDING = ("REGFM_A1 / REGFM_B1 records await verification of their PSS/E CON order; their parameters are in "
           "gfm_models.csv by name, not in the .dyr")
LEDGER = (
    "campus bundle: every genset governor is DEGOV1, the PSS/E diesel-engine model; a gas engine is approximated "
    "by it (owner decision)",
    "campus bundle: a genset's GENSAL X_d is its saturated xd_sat; X'd, X_q, X_l, the time constants and the "
    "saturation points are typical values (assumed)",
    "campus bundle: the grid is a GENCLS with H = 0, an infinite bus, until the islanding trip",
    "campus bundle: GFM droops and limits are converted from P_n to the inverter's S_n base: "
    "mp = R S_n / P_n, mq = k_q S_n / P_n, Pmax = P_n / S_n",
)
GRID = "GRID"


@dataclasses.dataclass
class CampusBundle:
    path: Path
    records: dict          # model -> number of records written
    drafted: pd.DataFrame  # unit, record, param, value, source
    omitted: list          # (unit, why)


def _source(campus, uid, param):
    p = campus.params
    row = p[(p["element"] == uid) & (p["param"] == param)]
    return f"campus ({row['source'].iloc[0]})" if len(row) else "campus"


def _genset_records(campus, uid, block, num, mid, mbase):
    """The genset's GENSAL, DEGOV1 and SEXS lines, and the values drafted."""
    v = block["values"]
    p = campus.params.set_index(["element", "param"])["value"]
    xd_pp = float(p[(uid, "xd_pp")])
    gensal = dict(GENSAL_TYPICAL)
    gensal.update(h_s=float(v["H_s"]), xd=float(v["xd_sat"]), xd_pp=xd_pp, xl=XL_SHARE * xd_pp,
                  xq=XQ_SHARE * float(v["xd_sat"]))
    gensal["xd_p"] = max(GENSAL_TYPICAL["xd_p"], XDP_OVER_XDPP * xd_pp)
    _check_physics(uid, "GENSAL", gensal, True, mbase)
    src = {"h_s": _source(campus, uid, "H_s"), "xd": _source(campus, uid, "xd_sat"),
           "xd_pp": _source(campus, uid, "xd_pp")}
    drafted = [(uid, "GENSAL", k, gensal[k], src.get(k, TYPICAL)) for k in DYR_CONS["GENSAL"]]
    lines = [f"{num:>6d} 'GENSAL' '{mid:<2s}' " + "".join(f"{gensal[k]:10.5f}" for k in DYR_CONS["GENSAL"]) + " /"]

    iso = block.get("governor") == "isochronous"
    droop = 0.0 if iso else float(v["droop_pct"]) / 100.0
    gov = dict(DEGOV1_TYPICAL)
    gov.update(droop=droop, k=1.0 / (ISO_DROOP if iso else droop),
               tmax=float(campus.units.at[uid, "p_mw"]) / mbase)
    gsrc = {"droop": "campus (isochronous)" if iso else _source(campus, uid, "droop_pct"),
            "k": "1 / DROOP" if not iso else f"1 / {ISO_DROOP} (assumed)", "tmax": "P_n / MBASE"}
    drafted += [(uid, "DEGOV1", k, gov[k], gsrc.get(k, TYPICAL)) for k in ("m", "t1", "t2", "t3", "k", "t4", "t5",
                                                                           "t6", "td", "tmax", "tmin", "droop", "te")]
    lines.append(format_record(num, mid, "DEGOV1", gov))

    if block["excitation"] != "none":
        exc = dict(SEXS_TYPICAL, emax=SEXS_EMAX[block["excitation"]])
        drafted += [(uid, "SEXS", k, exc[k], f"typical for {block['excitation']} (assumed)" if k == "emax" else TYPICAL)
                    for k in ("ta_tb", "tb", "k", "te", "emin", "emax")]
        lines.append(format_record(num, mid, "SEXS", exc))
    return lines, drafted


def _gfm_rows(campus, uid, block, num, mid):
    """The REGFM parameters of one GFM converter, by name, with sources."""
    u = campus.units.loc[uid]
    v = block["values"]
    p_n, s_n = float(u["p_mw"]), float(u["s_mva"])
    model = "REGFM_A1" if block["control"] == "gfm_droop" else "REGFM_B1"
    own = {"mp": v["droop_pct"] / 100.0 * s_n / p_n, "mq": v["q_droop_pct"] / 100.0 * s_n / p_n,
           "ImaxF": v["i_max_pu"]}
    srcs = {"mp": _source(campus, uid, "droop_pct"), "mq": _source(campus, uid, "q_droop_pct"),
            "ImaxF": _source(campus, uid, "i_max_pu")}
    if model == "REGFM_A1":
        own.update(Pmax=p_n / s_n, Pmin=-p_n / s_n if u["kind"] == "bess" else 0.0, TPf=v["tau_f_s"])
        srcs.update(Pmax="campus P_n / S_n", Pmin="campus P_n / S_n", TPf=_source(campus, uid, "tau_f_s"))
        example = REGFM_A1_EXAMPLE
    else:
        own.update(H=v["H_v_s"], ESFlag=1 if u["kind"] == "bess" else 0)
        srcs.update(H=_source(campus, uid, "H_v_s"), ESFlag="campus kind")
        example = REGFM_B1_EXAMPLE
    rows = [{"unit_id": uid, "bus_number": num, "machine_id": mid, "model": model, "parameter": k,
             "value": float(val), "base": f"{s_n:g} MVA", "source": srcs[k]} for k, val in own.items()]
    rows += [{"unit_id": uid, "bus_number": num, "machine_id": mid, "model": model, "parameter": k,
              "value": float(val), "base": f"{s_n:g} MVA", "source": SPEC[model]}
             for k, val in example.items() if k not in own]
    return rows


def export_campus_bundle(outdir, campus, rows, hour, ledger=()) -> CampusBundle:
    """Write the bundle for one island ``hour`` (a label) from that hour's
    rows of the campus hourly table. ``ledger`` adds the caller's entries
    to the README's."""
    case = f"{campus.name}_{hour}"
    out = Path(outdir) / case
    out.mkdir(parents=True, exist_ok=True)
    hour_campus = dataclasses.replace(campus, net=copy.deepcopy(campus.net))
    apply_hour(hour_campus, rows)
    net = hour_campus.net
    f_hz = float(net.f_hz)
    write_raw(net, out / f"{case}.raw", title=f"{campus.name} hour {hour}", f_hz=f_hz)

    lines, drafted, gfm, omitted = [], [], [], []
    records = {"GENSAL": 0, "DEGOV1": 0, "SEXS": 0, "GENCLS": 0}
    grid_id = None
    for uid, num, mid, mbase, _ in machine_records(net):
        if uid == GRID:
            lines.append(format_record(num, mid, "GENCLS", {"h": 0.0, "d": 0.0}))
            records["GENCLS"] += 1
            grid_id = (num, mid)
            continue
        if uid not in campus.units.index:
            omitted.append((uid, "a STATCOM: no dynamic model is drafted for it"))
            continue
        kind = campus.units.at[uid, "kind"]
        block = campus.island["units"].get(uid)
        if kind == "genset":
            if block is None:
                omitted.append((uid, "a genset without island data: no H, droop or excitation to draft from"))
                continue
            gl, gd = _genset_records(campus, uid, block, num, mid, mbase)
            lines += gl
            drafted += gd
            for ln in gl:
                records[ln.split("'")[1]] += 1
        elif kind == "ups":
            omitted.append((uid, "a UPS: it rides on its battery and feeds no network model"))
        elif block is not None and block.get("control") in ("gfm_droop", "gfm_vsm"):
            gfm += _gfm_rows(campus, uid, block, num, mid)
        else:
            omitted.append((uid, "a grid-following converter: REGCA1/REECA1 need the OEM's parameters, none is "
                                 "invented"))
    with open(out / f"{case}.dyr", "w", newline="\n") as f:
        f.write("".join(f"{ln}\n" for ln in lines))
    gfm_df = pd.DataFrame(gfm, columns=["unit_id", "bus_number", "machine_id", "model", "parameter", "value", "base",
                                        "source"])
    gfm_df.to_csv(out / "gfm_models.csv", index=False)
    drafted_df = pd.DataFrame(drafted, columns=["unit", "record", "param", "value", "source"])
    _readme(out / "README.md", campus, hour, grid_id, records, drafted_df, gfm_df, omitted, list(ledger))
    manifest = {"case": case, "hour": str(hour), "f_hz": f_hz,
                "files": [f"{case}.raw", f"{case}.dyr", "gfm_models.csv", "README.md", "manifest.json"],
                "records": records, "gfm_units": sorted(gfm_df["unit_id"].unique().tolist()),
                "omitted": [u for u, _ in omitted], "pending_verification": PENDING,
                "event": {"kind": "islanding", "trip_machine": list(grid_id) if grid_id else None, "t_s": 0.0}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return CampusBundle(out, records, drafted_df, omitted)


def _readme(path, campus, hour, grid_id, records, drafted, gfm, omitted, ledger):
    if grid_id is None:
        raise ContractError("the campus net has no grid machine to trip; the islanding event cannot be named")
    lines = [f"# {campus.name}: island hour {hour}", "",
             "A PSS/E v33 handoff for the client-grade island study (island plan I8). The first-pass "
             "checks that chose this hour are screenings; this case is for the study that decides.", "",
             "## Event", "",
             f"Islanding: trip the grid machine at bus {grid_id[0]}, ID '{grid_id[1]}', at t = 0 "
             "(the .raw is the hour before it, grid-connected).", "",
             "## Records", ""]
    lines += [f"- {model}: {n}" for model, n in records.items()]
    lines += [f"- grid-forming converters in gfm_models.csv: {gfm['unit_id'].nunique()}", "",
              "## Pending verification", "", f"- {PENDING}", "",
              "## Drafted values", "",
              "Every value below was drafted, not given by a datasheet for this unit; replace them with the "
              "OEM's before the study is relied on.", "",
              "| unit | record | parameter | value | source |", "|---|---|---|---|---|"]
    lines += [f"| {r.unit} | {r.record} | {r.param} | {r.value:g} | {r.source} |" for r in drafted.itertuples()]
    lines += ["", "Grid-forming parameters and their sources are in gfm_models.csv.", "",
              "## Omissions", ""]
    lines += [f"- {uid}: {why}" for uid, why in omitted] or ["- none"]
    lines += ["", "## Ledger", ""] + [f"- {entry}" for entry in (*LEDGER, *ledger)]
    Path(path).write_text("\n".join(lines) + "\n")
