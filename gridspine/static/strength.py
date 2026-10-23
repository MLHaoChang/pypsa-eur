"""Stage 2: the short-circuit-ratio pre-check at every inverter bus.

    SCR = S_k'' at the connection bus / installed inverter MVA at that bus

``scr`` is plain SCR only. The impedance screen and the RoCoF -> EMT flag
are spec phase 4; a pre-check that quietly becomes a grid-strength study is
scope creep with a compliance-shaped tail.

``strength_table`` (datacenter-grid-readiness issue 11) is the separate
strength study per point of connection (PoC): SCR, ESCR and WSCR, intact and
at the worst outage, one row per development stage. Its fault levels come
from ``static/strength_outages.py``; this module only does the arithmetic:

    ESCR = (S_sc - Q_c) / P_n
    WSCR = sum_i(S_sc,i * P_i) / (sum_i P_i)^2   over a user-given group

Every row is a static screen and says so; the binding answer is the RMS/EMT
study.

Two rulings, both ledgered:

* The denominator is INSTALLED capacity — the templates' inverter ``mbase_mva``,
  which Task 10 records as the RES_LEDGER installed MW read as MVA — never the
  dispatched hour. SCR is a property of the network's strength at the bus;
  dividing by a curtailed output would report a weak bus as strong.
* The bands are REPORTED thresholds, not pass/fail gates. This stage has no
  standing to fail a bus; it tells the reader which buses to look at.

The numerator is whichever 60909 case the caller supplies; the table must be a
single case and the case is carried in the output. Weak-grid screening
conventionally uses the MINIMUM fault level, but that choice belongs to the
driver, not here.

Pure pandas — no engine import, though ``static/`` would allow one.
"""
import dataclasses
import math

import pandas as pd

from gridspine.schema.contingency import validate_fault_levels
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import UnitTemplates

#: Ascending band edges; a value ON an edge belongs to the upper band.
SCR_BANDS = (2.0, 3.0, 5.0)
BAND_NAMES = ("very_weak", "weak", "moderate", "strong")

SCR_LEDGER = (
    "SCR = S_k'' at the connection bus / INSTALLED inverter MVA at that bus; "
    "capacity is the template mbase_mva (installed MW read as MVA), never the "
    "dispatched output — dividing by a curtailed hour would report a weak bus "
    "as strong (assumed scope)",
    "SCR bands <2 very_weak, 2-3 weak, 3-5 moderate, >=5 strong are conventional "
    "reporting thresholds, not pass/fail gates; plain SCR only, WSCR/ESCR and the "
    "impedance screen are phase 4",
    "the 60909 case feeding the numerator is the caller's choice and is carried "
    "in the table; weak-grid screening conventionally uses the minimum case",
)

OUTPUT_COLUMNS = ("bus", "case", "ibr_mva", "sk_mva", "scr", "band")


def band(value: float) -> str:
    for edge, name in zip(SCR_BANDS, BAND_NAMES):
        if value < edge:
            return name
    return BAND_NAMES[-1]


def scr(fault_levels: pd.DataFrame, registry: pd.DataFrame, templates: UnitTemplates) -> pd.DataFrame:
    fl = validate_fault_levels(fault_levels)
    cases = sorted(set(fl["case"]))
    if len(cases) != 1:
        raise ContractError(
            f"fault_levels must hold a single 60909 case, got {cases}; SCR at one "
            "bus from two cases is two different numbers"
        )
    case = cases[0]
    by_bus = fl.set_index("bus")

    res = registry[registry["kind"] == "res"]
    no_template = [u for u in res.index if u not in templates.units.index]
    if no_template:
        raise ContractError(f"res unit(s) with no template row: {no_template}")
    not_inverter = [
        u for u in res.index if templates.units.at[u, "model"] != "inverter"
    ]
    if not_inverter:
        raise ContractError(
            f"res unit(s) whose template model is not 'inverter': {not_inverter}"
        )
    capacity = pd.Series(
        [float(templates.units.at[u, "mbase_mva"]) for u in res.index], index=res.index
    )
    ibr_mva = capacity.groupby(res["bus"].values).sum()

    no_fault_level = sorted(b for b in ibr_mva.index if b not in by_bus.index)
    if no_fault_level:
        raise ContractError(f"no fault level for RES bus(es): {no_fault_level}")

    rows = []
    for bus in sorted(ibr_mva.index):
        sk = float(by_bus.at[bus, "sk_mva"])
        cap = float(ibr_mva[bus])
        value = sk / cap
        if not math.isfinite(value):
            raise ContractError(f"non-finite SCR at {bus}: sk_mva={sk}, ibr_mva={cap}")
        rows.append({
            "bus": bus, "case": case, "ibr_mva": cap, "sk_mva": sk,
            "scr": value, "band": band(value),
        })
    return pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))


# --- the strength study (issue 11): SCR, ESCR, WSCR per PoC and stage ---------

STUDY_LABEL = "static screen; binding answer is the RMS/EMT study"

#: Compensation kinds that count towards Q_c.
Q_C_KINDS = ("capacitor_bank",)

NO_COMPENSATION = "no compensation: ESCR = SCR"
NO_FILTERS = "harmonic filters are not in the campus schema, so not counted in Q_c"
NOT_CONNECTED = "not connected at this stage"

STUDY_LEDGER = (
    "ESCR = (S_sc - Q_c) / P_n with Q_c the capacitor banks behind the PoC at "
    "full rating (all steps); shunt reactors and STATCOMs are excluded, which is "
    "the conservative side (a reactor would raise ESCR) (assumed scope)",
    NO_FILTERS,
    "WSCR = sum(S_sc,i * P_i) / (sum P_i)^2 over the PoCs the user groups as "
    "electrically close; no automatic clustering",
    "S_sc is the IEC 60909 minimum case, computed once per network: the "
    "development stages change P_n only, not the network (assumed)",
    "S_sc at a PoC excludes the converter units at the assessed PoCs: SCR "
    "measures the grid's strength without the plant it is judged against. They "
    "are switched off explicitly, and pandapower 3.1.2's IEC 60909 minimum case "
    "leaves current-source units out anyway (probed), so S_sc equals "
    "shortcircuit.fault_levels' minimum case at every PoC",
    "outages are the N-1 branches (and optionally N-2 pairs) of "
    "static/contingency_set.py, read-only; above the user's cap the order is "
    "pre-screened by outaging the branch (pair) with the largest fault "
    "contribution at the PoC",
    "an outage that leaves a PoC with no fault level (NaN S_sc) disconnects it; "
    "it is listed in islanding_outages, not taken as the worst SCR; radial loss "
    "is the planning-criteria question of issue 13",
    "bands are strength.band's reporting thresholds applied to SCR, ESCR and "
    "WSCR alike; not pass/fail gates",
)

POC_COLUMNS = (
    "stage", "poc", "bus", "group", "p_mw", "q_c_mvar",
    "sk_intact_mva", "scr_intact", "escr_intact", "band_intact", "escr_band_intact",
    "worst_outage", "sk_worst_mva", "scr_worst", "escr_worst", "band_worst", "escr_band_worst",
    "islanding_outages", "outage_method", "note", "label",
)
GROUP_COLUMNS = (
    "stage", "group", "pocs", "p_mw",
    "wscr_intact", "band_intact", "worst_outage", "wscr_worst", "band_worst",
    "islanding_outages", "outage_method", "note", "label",
)


@dataclasses.dataclass(frozen=True)
class StrengthStudy:
    """``pocs``: one row per (stage, PoC); ``groups``: one row per (stage,
    group) with the WSCR. Columns ``POC_COLUMNS`` and ``GROUP_COLUMNS``."""
    pocs: pd.DataFrame
    groups: pd.DataFrame


def escr(sk_mva: float, q_c_mvar: float, p_mw: float) -> float:
    """(S_sc - Q_c) / P_n."""
    return (sk_mva - q_c_mvar) / p_mw


def wscr(sk_mva, p_mw) -> float:
    """sum(S_sc,i * P_i) / (sum P_i)^2 over aligned sequences."""
    sk, p = list(sk_mva), list(p_mw)
    total = sum(p)
    return sum(s * w for s, w in zip(sk, p)) / total ** 2


def q_c_by_poc(compensation: pd.DataFrame, poc_of_bus: dict) -> pd.DataFrame:
    """Q_c per PoC from a campus ``compensation`` table (index ``name``:
    ``kind``, ``bus``, ``q_mvar``). ``poc_of_bus`` maps each compensation bus
    to the PoC it sits behind. A capacitor bank whose bus maps to no PoC is
    refused: pooling it silently would credit the wrong connection. Returns
    index ``poc``: ``q_c_mvar``, ``banks``."""
    rows = {}
    for name, rec in compensation.iterrows():
        if rec["kind"] not in Q_C_KINDS:
            continue
        bus = str(rec["bus"])
        if bus not in poc_of_bus:
            raise ContractError(
                f"capacitor bank {name} at bus {bus} sits behind no PoC; give its "
                "bus in the bus-to-PoC map"
            )
        q = float(rec["q_mvar"])
        if not math.isfinite(q) or q <= 0:
            raise ContractError(f"capacitor bank {name}: q_mvar must be finite and > 0, got {q}")
        poc = poc_of_bus[bus]
        q_sum, banks = rows.get(poc, (0.0, []))
        rows[poc] = (q_sum + q, banks + [str(name)])
    return pd.DataFrame(
        [{"poc": p, "q_c_mvar": q, "banks": b} for p, (q, b) in rows.items()],
        columns=["poc", "q_c_mvar", "banks"],
    ).set_index("poc")


def _check_inputs(pocs, stages, q_c):
    for col in ("poc", "bus"):
        if col not in pocs.columns:
            raise ContractError(f"pocs needs a {col!r} column")
    if pocs["poc"].duplicated().any():
        raise ContractError(f"duplicate PoC id(s): {sorted(pocs.loc[pocs['poc'].duplicated(), 'poc'])}")
    for col in ("stage", "poc", "p_mw"):
        if col not in stages.columns:
            raise ContractError(f"stages needs a {col!r} column")
    unknown = sorted(set(stages["poc"]) - set(pocs["poc"]))
    if unknown:
        raise ContractError(f"stages name PoC(s) not in pocs: {unknown}")
    if stages.duplicated(subset=["stage", "poc"]).any():
        raise ContractError("stages has more than one row for a (stage, poc)")
    p = pd.to_numeric(stages["p_mw"], errors="coerce")
    bad = p.isna() | ~p.map(math.isfinite) | (p < 0)
    if bad.any():
        raise ContractError(
            f"stage p_mw must be finite and >= 0, got {stages.loc[bad, 'p_mw'].tolist()}"
        )
    unknown_q = sorted(set(q_c.index) - set(pocs["poc"]))
    if unknown_q:
        raise ContractError(f"q_c names PoC(s) not in pocs: {unknown_q}")
    for q in q_c["q_c_mvar"] if "q_c_mvar" in q_c.columns else []:
        if not math.isfinite(float(q)) or float(q) < 0:
            raise ContractError(f"q_c_mvar must be finite and >= 0, got {q}")


def _islanding_note(n: int, what: str = "this PoC") -> str:
    return (f"{n} outage(s) disconnect {what}; see islanding_outages; radial "
            "loss is the planning-criteria contingency check")


def _worst(sk_by_outage: pd.Series):
    """(outage, sk) with the smallest finite S_sc, ties to the first id; and the
    outages that leave it NaN. (None, None) when every outage islands it."""
    islanding = sorted(sk_by_outage.index[sk_by_outage.isna()])
    finite = sk_by_outage.dropna()
    if finite.empty:
        return None, None, islanding
    finite = finite.sort_index(kind="mergesort")
    outage = finite.idxmin()
    return outage, float(finite[outage]), islanding


def _method_notes(outage_method: str) -> list:
    return ["pre-screen used: not every outage was solved"] if "pre-screen" in outage_method else []


def _band_or_none(value):
    return None if value is None else band(value)


def strength_table(pocs: pd.DataFrame, stages: pd.DataFrame, intact_sk: pd.Series,
                   outage_sk: pd.DataFrame, outage_method: str,
                   q_c: pd.DataFrame = None) -> StrengthStudy:
    """SCR, ESCR and WSCR per PoC and stage, intact and at the worst outage.

    ``pocs``: ``poc``, ``bus`` and optionally ``group`` (PoCs the user calls
    electrically close; None for none). ``stages``: ``stage``, ``poc``,
    ``p_mw``, in the order the stages are to be reported; a PoC absent from a
    stage is not connected at it. ``intact_sk``: S_sc (MVA) by bus.
    ``outage_sk``: index outage id, one column per PoC bus, NaN where the
    outage disconnects that bus. ``q_c``: from ``q_c_by_poc``; a PoC absent
    from it has none.
    """
    if q_c is None:
        q_c = pd.DataFrame(columns=["q_c_mvar", "banks"], index=pd.Index([], name="poc"))
    _check_inputs(pocs, stages, q_c)
    if "group" not in pocs.columns:
        pocs = pocs.assign(group=None)
    missing = sorted(set(pocs["bus"]) - set(intact_sk.index))
    if missing:
        raise ContractError(f"no intact fault level for PoC bus(es): {missing}")
    missing = sorted(set(pocs["bus"]) - set(outage_sk.columns))
    if missing:
        raise ContractError(f"no outage fault levels for PoC bus(es): {missing}")
    bad = [b for b in pocs["bus"] if not (math.isfinite(float(intact_sk[b])) and float(intact_sk[b]) > 0)]
    if bad:
        raise ContractError(f"intact S_sc must be finite and > 0 at PoC bus(es): {bad}")

    stage_order = list(dict.fromkeys(stages["stage"]))
    p_at = {(r.stage, r.poc): float(r.p_mw) for r in stages.itertuples(index=False)}
    poc_rows, group_rows = [], []
    for stage in stage_order:
        for rec in pocs.itertuples(index=False):
            poc, bus, group = rec.poc, rec.bus, rec.group
            p = p_at.get((stage, poc), 0.0)
            q = float(q_c.at[poc, "q_c_mvar"]) if poc in q_c.index else 0.0
            sk = float(intact_sk[bus])
            outage, sk_w, islanding = _worst(outage_sk[bus])
            notes = [] if q > 0 else [NO_COMPENSATION]
            notes.append(NO_FILTERS)
            notes += _method_notes(outage_method)
            row = {"stage": stage, "poc": poc, "bus": bus, "group": group, "p_mw": p,
                   "q_c_mvar": q, "sk_intact_mva": sk, "worst_outage": outage,
                   "sk_worst_mva": sk_w, "islanding_outages": islanding,
                   "outage_method": outage_method, "label": STUDY_LABEL}
            ratios = dict.fromkeys(("scr_intact", "escr_intact", "scr_worst", "escr_worst"))
            if p == 0:
                notes.insert(0, NOT_CONNECTED)
            else:
                ratios["scr_intact"], ratios["escr_intact"] = sk / p, escr(sk, q, p)
                if sk_w is not None:
                    ratios["scr_worst"], ratios["escr_worst"] = sk_w / p, escr(sk_w, q, p)
                if islanding:
                    notes.append(_islanding_note(len(islanding)))
            row.update(ratios)
            row.update(band_intact=_band_or_none(ratios["scr_intact"]),
                       escr_band_intact=_band_or_none(ratios["escr_intact"]),
                       band_worst=_band_or_none(ratios["scr_worst"]),
                       escr_band_worst=_band_or_none(ratios["escr_worst"]),
                       note="; ".join(notes))
            poc_rows.append(row)

        grouped = pocs[pocs["group"].notna()]
        for group in list(dict.fromkeys(grouped["group"])):
            members = grouped[grouped["group"] == group]
            p = [p_at.get((stage, poc), 0.0) for poc in members["poc"]]
            buses = [b for b, w in zip(members["bus"], p) if w > 0]
            weights = [w for w in p if w > 0]
            row = {"stage": stage, "group": group, "pocs": list(members["poc"]),
                   "p_mw": sum(p), "wscr_intact": None, "worst_outage": None,
                   "wscr_worst": None, "islanding_outages": [],
                   "outage_method": outage_method, "label": STUDY_LABEL}
            notes = _method_notes(outage_method)
            if not weights:
                notes.append(NOT_CONNECTED)
            else:
                row["wscr_intact"] = wscr([float(intact_sk[b]) for b in buses], weights)
                sub = outage_sk[buses]
                islands = sub.isna().any(axis=1)
                row["islanding_outages"] = sorted(sub.index[islands])
                by_outage = pd.Series(
                    [wscr(r, weights) for r in sub[~islands].itertuples(index=False)],
                    index=sub.index[~islands], dtype=float,
                )
                outage, value, _ = _worst(by_outage)
                row["worst_outage"], row["wscr_worst"] = outage, value
                if row["islanding_outages"]:
                    notes.append(_islanding_note(len(row["islanding_outages"]), "a PoC of this group"))
            row.update(band_intact=_band_or_none(row["wscr_intact"]),
                       band_worst=_band_or_none(row["wscr_worst"]), note="; ".join(notes))
            group_rows.append(row)

    return StrengthStudy(
        pocs=pd.DataFrame(poc_rows, columns=list(POC_COLUMNS)),
        groups=pd.DataFrame(group_rows, columns=list(GROUP_COLUMNS)),
    )
