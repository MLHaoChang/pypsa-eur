"""The intent comparison's project-held inputs, resolved once for the form's prefill and for the run.

Walkthrough bugs 05 and 06 (asset investment, 2026-11-17). The comparison's inputs that duplicate a project value
come from that value, so the prefill (`GET .../intent-comparison/defaults`) and the run (`POST`) read one ladder,
`resolve`: the request's own value, else the project's, else an assumed one. Each value carries its source.

- `link`: the Link whose `eh_role` marks the grid import (the point of connection);
- `ramp_mw_per_min`: the grid-readiness scorecard's connection ramp limit (the user's limit, else the template's
  ledger, else the generic placeholder), the number Grid connection › Connection limits shows;
- `envelope`: the DC load's stored dynamic envelope (`dc_load_profile.read_envelope`), all phases;
- `pq`: the point-of-connection values the PQ and Pst checks read (`pcs_statcom.LINK_COLUMNS`): the request's,
  else the link's saved tags, else assumed values. Assumed values run only when the request asks for them by
  sending `pq` (an empty `{}` takes every project or assumed value); a request without `pq` on a link that holds
  none is refused, naming the fields. They are applied to the comparison's copy of the network only (`apply_pq`); the
  project's saved network is never tagged, so its own optimisation does not change.

Nothing here imports a router.
"""

from __future__ import annotations

import math
import re

from models.energy_hub import EH_IMPORT_ROLES
from services.solver import pcs_statcom

SOURCE_REQUEST = "request"
SOURCE_PROJECT = "project"
#: What `defaults` says each value is, for the form's one word after the field (`intentSources.ts` maps them;
#: its parity test reads this tuple).
KINDS = ("project", "assumed", "load_envelope", "grid_code")
#: Request `pq` field → the Link tag column it sets.
PQ_COLUMNS = {
    "x_pu": "pq_x_pu",
    "pf": "pq_pf",
    "s_n_mva": "pq_s_n_mva",
    "p_cap_mw": "pq_p_cap_mw",
    "pf_req": "pq_pf_req",
    "statcom_capital_cost": "statcom_capital_cost",
}
#: The plain words for each `pq` field, as the form labels it.
PQ_LABELS = {
    "x_pu": "transformer reactance (p.u.)",
    "pf": "load power factor",
    "s_n_mva": "transformer rating (MVA)",
    "p_cap_mw": "import cap (MW)",
    "pf_req": "required power factor",
    "statcom_capital_cost": "STATCOM cost (€/Mvar/yr)",
}
#: Generic engineering values where the project holds none (SAGC-1: assumed, marked as such).
ASSUMED_X_PU = 0.12
ASSUMED_PF = 0.95
ASSUMED_PF_REQ = 0.95
#: The defaults-pack STATCOM the cost is read from: the smallest unit, so the dearest per Mvar.
STATCOM_PACK_TECH = "statcom.sc_33_5m"
STATCOM_PACK_MVAR = 5.0


class IntentInputsRefusal(ValueError):
    """The comparison's inputs cannot be resolved or applied as given."""


def poc_link(n) -> str | None:
    """The Link marked as the grid import, or None."""
    if "eh_role" not in n.links.columns:
        return None
    roles = n.links["eh_role"].fillna("").astype(str).str.strip()
    hits = roles.index[roles.isin(EH_IMPORT_ROLES)]
    return str(hits[0]) if len(hits) else None


def _reach_mw(n, link: str) -> float | None:
    """How far the link's flow can reach, in MW: its rating times its largest per-unit bound. An extendable
    link with no finite `p_nom_max` reads its `p_nom`; None when that gives no finite, positive reach."""
    s = n.links.loc[link]
    p_nom = float(s["p_nom"])
    if s["p_nom_extendable"] and math.isfinite(float(s["p_nom_max"])):
        p_nom = float(s["p_nom_max"])
    pu = max(
        n.get_switchable_as_dense("Link", "p_max_pu")[link].abs().max(),
        n.get_switchable_as_dense("Link", "p_min_pu")[link].abs().max(),
    )
    reach = p_nom * float(pu)
    return reach if math.isfinite(reach) and reach > 0 else None


def statcom_cost(rate: float) -> tuple[float, str]:
    """The STATCOM's annual cost per Mvar from the defaults pack: overnight per Mvar times the capital recovery
    factor at the project's discount rate, plus the pack's fixed O&M share. (value, note)."""
    from services.finance.replacements import annuity
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack()
    key = f"{STATCOM_PACK_TECH}.investment"
    overnight = pack.value(f"{key}.overnight").value / STATCOM_PACK_MVAR
    life = pack.value(f"{key}.lifetime").value
    fom = pack.value(f"{key}.fom_share").value
    value = overnight * (annuity(rate, life) + fom)
    note = (
        f"assumed (defaults pack {pack.version}, {key}.overnight / {STATCOM_PACK_MVAR:g} Mvar, lifetime "
        f"{life:g} years, fixed O&M {fom:.0%}/yr), annualised at the project's discount rate {rate:.1%}"
    )
    return value, note


def _project_pq(n, link: str, rate: float) -> dict:
    """The link's saved PQ tags, else the assumed values: {field: {value, source}}."""
    tags, out = None, {}
    _, links = pcs_statcom.pcs_statcom_tags(n)
    if links is not None and link in links.index:
        tags = links.loc[link]
    reach = _reach_mw(n, link)
    assumed = {
        "x_pu": (
            ASSUMED_X_PU,
            "assumed: a typical transformer short-circuit reactance",
        ),
        "pf": (ASSUMED_PF, "assumed: a typical load power factor"),
        "s_n_mva": (reach, "assumed: the connection's rating"),
        "p_cap_mw": (reach, "assumed: the connection's rating"),
        "pf_req": (
            ASSUMED_PF_REQ,
            "assumed: no grid code value; a typical connection-offer requirement",
        ),
    }
    for field, col in PQ_COLUMNS.items():
        v = None if tags is None else tags.get(col)
        if v is not None and math.isfinite(float(v)):
            out[field] = {
                "value": float(v),
                "source": SOURCE_PROJECT,
                "kind": "project",
            }
        elif field == "statcom_capital_cost":
            value, note = statcom_cost(rate)
            out[field] = {"value": value, "source": note, "kind": "assumed"}
        else:
            value, note = assumed[field]
            if value is None:
                out[field] = {
                    "value": None,
                    "source": None,
                    "kind": "assumed",
                    "reason": "The connection has no finite rating; enter it.",
                }
            else:
                out[field] = {"value": float(value), "source": note, "kind": "assumed"}
    return out


def resolve(
    n, req: dict, *, ramp_row: dict | None, envelope: dict | None, rate: float
) -> tuple[dict, dict]:
    """(inputs, sources). `inputs` is `req` with `link`, `ramp_mw_per_min`, `envelope` and `pq` filled from the
    project where `req` leaves them out; `sources[field]` says where each of those came from (`pq` per field).
    `req` is not changed."""
    inputs, sources = dict(req), {}

    if req.get("link") is not None:
        inputs["link"], sources["link"] = req["link"], SOURCE_REQUEST
    else:
        link = poc_link(n)
        if link is None:
            raise IntentInputsRefusal(
                "Campus link: no link is marked as the grid import; choose the campus link"
            )
        inputs["link"], sources["link"] = link, SOURCE_PROJECT

    if req.get("ramp_mw_per_min") is not None:
        sources["ramp_mw_per_min"] = SOURCE_REQUEST
    elif ramp_row is not None and ramp_row.get("limit") is not None:
        inputs["ramp_mw_per_min"], sources["ramp_mw_per_min"] = (
            float(ramp_row["limit"]),
            ramp_row["source"],
        )
    else:
        raise IntentInputsRefusal(
            "Grid ramp limit: the project has none; enter it in MW/min"
        )

    if req.get("envelope") is not None:
        sources["envelope"] = SOURCE_REQUEST
    elif envelope is not None:
        inputs["envelope"], sources["envelope"] = (
            envelope,
            "the DC load's dynamic envelope",
        )
    else:
        raise IntentInputsRefusal(
            "Load swing, oscillation amplitude and period: the project has no DC load "
            "envelope; enter them"
        )

    link = inputs["link"]
    if link not in n.links.index:
        raise IntentInputsRefusal(
            f"Campus link: {link} is not in the network; choose the campus link"
        )
    given = req.get("pq")
    project = _project_pq(n, link, rate)
    if given is None and any(v["source"] != SOURCE_PROJECT for v in project.values()):
        missing = [
            PQ_LABELS[f] for f, v in project.items() if v["source"] != SOURCE_PROJECT
        ]
        raise IntentInputsRefusal(
            f"Point of connection: the project holds no {', '.join(missing)}; enter them "
            "or use the assumed values the form shows"
        )
    if given is not None and not isinstance(given, dict):
        raise IntentInputsRefusal(
            "Point of connection: the values must be given by name"
        )
    unknown = sorted(set(given or {}) - set(PQ_COLUMNS))
    if unknown:
        raise IntentInputsRefusal(
            f"Point of connection: unknown value(s) {', '.join(unknown)}"
        )
    pq, pq_sources = {}, {}
    for field in PQ_COLUMNS:
        if given is not None and given.get(field) is not None:
            pq[field], pq_sources[field] = given[field], SOURCE_REQUEST
        elif project[field]["value"] is None:
            raise IntentInputsRefusal(
                f"Point of connection: {PQ_LABELS[field]}: the connection has no finite rating; enter it"
            )
        else:
            pq[field], pq_sources[field] = (
                project[field]["value"],
                project[field]["source"],
            )
    inputs["pq"], sources["pq"] = pq, pq_sources
    return inputs, sources


def defaults(n, *, ramp_row: dict | None, envelope: dict | None, rate: float) -> dict:
    """What `resolve` runs on a request that leaves the project-held inputs out, as {field: {value, source}};
    a field the project cannot supply is {value: None, source: None, reason}."""
    out: dict = {}
    link = poc_link(n)
    out["link"] = (
        {"value": link, "source": SOURCE_PROJECT, "kind": "project"}
        if link is not None
        else {
            "value": None,
            "source": None,
            "reason": "No link is marked as the grid import.",
        }
    )
    if ramp_row is not None and ramp_row.get("limit") is not None:
        out["ramp_mw_per_min"] = {
            "value": float(ramp_row["limit"]),
            "source": ramp_row["source"],
            "kind": "assumed" if ramp_row.get("assumed") else "project",
        }
    else:
        out["ramp_mw_per_min"] = {
            "value": None,
            "source": None,
            "reason": "The project has no ramp limit.",
        }
    out["envelope"] = (
        {
            "value": envelope,
            "source": "the DC load's dynamic envelope",
            "kind": "load_envelope",
        }
        if envelope is not None
        else {
            "value": None,
            "source": None,
            "reason": "The project has no DC load envelope.",
        }
    )
    out["pq"] = _project_pq(n, link, rate) if link is not None else {}
    return out


def apply_pq(n, inputs: dict) -> dict:
    """Tag `inputs["link"]` of `n` (the comparison's own copy) with `inputs["pq"]` and check the tags with the
    rows' own check, `pcs_statcom._check`; return `inputs` without `pq` (the `SizingInputs` fields). A value the
    check refuses is an `IntentInputsRefusal` naming the field in plain words."""
    rest = {k: v for k, v in inputs.items() if k != "pq"}
    pq = inputs["pq"]
    try:
        values = {f: float(pq[f]) for f in PQ_COLUMNS}
    except (TypeError, ValueError):
        raise IntentInputsRefusal("Point of connection: every value must be a number")
    bad = [PQ_LABELS[f] for f, v in values.items() if not math.isfinite(v)]
    if bad:
        raise IntentInputsRefusal(
            f"Point of connection: {', '.join(bad)} must be a finite number"
        )
    # The chosen link is the one grid connection the PQ cut reads: tags saved on any other link are cleared in
    # this copy, so the check does not refuse two tagged links.
    tag_cols = [
        c
        for c in pcs_statcom.LINK_COLUMNS + pcs_statcom.OPTIONAL_LINK_COLUMNS
        if c in n.links.columns
    ]
    others = n.links.index != inputs["link"]
    n.links.loc[others, tag_cols] = float("nan")
    pcs_statcom.tag_pq_link(n, inputs["link"], **values)
    try:
        pcs_statcom._check(n)
    except pcs_statcom.PcsStatcomRefusal as exc:
        text = str(exc)
        named = [
            PQ_LABELS[f]
            for f, col in PQ_COLUMNS.items()
            if re.search(rf"\b{col}\b", text)
        ]
        if named:
            raise IntentInputsRefusal(
                f"Point of connection: {', '.join(named)} out of range ({_plain(text)})"
            )
        raise IntentInputsRefusal(f"Point of connection: {_plain(text)}")
    return rest


def _plain(text: str) -> str:
    """The check's message with the tag columns in the form's words."""
    for field, col in sorted(PQ_COLUMNS.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(col, PQ_LABELS[field])
    return text
