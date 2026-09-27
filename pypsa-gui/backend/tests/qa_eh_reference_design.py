"""
QA: the Energy Hub reference design, end to end over HTTP — a
``weak_flexible`` study whose report carries the stages that were ``skipped``
until 2026-09-26 (plan docs/superpowers/plans/2026-09-26-eh-wire-skipped-stages.md).

Why a driver rather than more unit tests
----------------------------------------
``tests/test_energy_hub_*.py`` drive ``run_eh_study`` directly. None of them
pin the JOURNEY the panel and the chat tool take: install a network, set the
solver config, ``POST /api/results/eh_study``, poll ``GET /api/results/eh_study``
while the worker runs the whole default pipeline, then read the persisted
``GET /api/results/eh_reference_design`` and find, in ONE report,

* a finite ``mc_lole_h`` and a ``certification`` verdict (WP1),
* a frontier of at least three points, every cost ex-shed with a period
  basis (WP2),
* a non-empty, criticality-ranked ``fmea_top`` carrying the Link-primary
  note (WP3),
* a TEA whose LCOH is ``null`` + ``lcoh_status`` on a network with no
  electrolyser, and finite on one with (WP4),
* ``solves_consumed ≤ budget_solves`` (decision 17), and a JSON-clean body,
* the import SAMPLED rather than counted firm (plan 2026-09-27): the
  weak_flexible certification samples the PoC Link and the grid behind it
  (``import_model == "zonal"``); on a firm-vs-sampled fixture pair a
  reliable Link with q > 0 raises the MC LOLE over the firm block, while an
  islanded off_grid hub certifies to the same LOLE either way.

Runs in CI through ``tests/run_qa_drivers.py`` (``pixi run gui-qa-drivers``).
Exit 0 = every step passed.
"""
from __future__ import annotations

import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, and it must come first: `qa_support` pins the
# sandbox before anything imports `main` or `settings`.
from tests import qa_support          # noqa: E402

from tests.eh_stage_fixtures import (  # noqa: E402
    VOLL,
    certifiable_weak_network,
    electrolyser_network,
    firm_vs_sampled_import_pair,
)

PROJECT = "qa_eh_reference_design"
BUDGET_SOLVES = 30
MIN_FRONTIER_POINTS = 3

PASS = 0
FAIL = 0
#: Printed at the end — the findings note quotes these numbers.
WEAK_EVIDENCE: dict = {}


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _nonfinite(obj, path: str = "$", bad: list | None = None) -> list:
    """Every path holding a NaN or an infinity — a 500 at the wire."""
    bad = [] if bad is None else bad
    if isinstance(obj, bool):
        return bad
    if isinstance(obj, float) and not math.isfinite(obj):
        bad.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _nonfinite(v, f"{path}.{k}", bad)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _nonfinite(v, f"{path}[{i}]", bad)
    return bad


def _poll(path: str, *, timeout: float = 900.0) -> dict:
    c = qa_support.client()
    t0 = time.time()
    body: dict = {}
    while time.time() - t0 < timeout:
        r = c.get(path)
        if r.status_code == 204:
            return {"status": "no-content"}
        if r.status_code != 200:
            return {"status": f"http-{r.status_code}", "error": r.text[:300]}
        body = r.json()
        if body.get("status") != "running":
            return body
        time.sleep(0.25)
    return {"status": "timeout", **body}


def _configure(voll: float) -> None:
    c = qa_support.client()
    r = c.put("/api/simulation/solver_config",
              json={"solver_name": "highs", "voll": voll})
    _step("the solver config takes the VoLL", r.status_code == 200
          and r.json().get("voll") == voll, f"HTTP {r.status_code} {r.text[:200]}")


def _run_study(archetype: str, *, stages: list[str] | None = None) -> dict:
    c = qa_support.client()
    body: dict = {"archetype": archetype, "budget_solves": BUDGET_SOLVES}
    if stages is not None:
        body["stages"] = stages
    r = c.post("/api/results/eh_study", json=body)
    _step(f"the {archetype} study starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return {}
    study = _poll("/api/results/eh_study")
    _step(f"the {archetype} study finishes", study.get("status") == "done",
          f"status={study.get('status')} error={str(study.get('error'))[:200]}")
    return study


def _stage(report: dict, name: str) -> dict:
    for rec in (report.get("pipeline") or {}).get("stages") or []:
        if rec.get("stage") == name:
            return rec
    return {}


# ── the journey ───────────────────────────────────────────────────────────

def section_1_weak_flexible() -> None:
    print("\n[1] weak_flexible — the default pipeline over HTTP")
    c = qa_support.client()
    qa_support.install_network(certifiable_weak_network())
    qa_support.save_project(PROJECT)
    _configure(VOLL)
    study = _run_study("weak_flexible")
    if study.get("status") != "done":
        return
    r = c.get("/api/results/eh_reference_design")
    _step("the report is persisted for GET", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    rep = r.json()
    _step("the report is JSON-clean (no NaN/inf reaches the wire)",
          not _nonfinite(rep), str(_nonfinite(rep)[:5]))
    _step("the study status carries the same report",
          (study.get("report") or {}).get("pack_hash") == rep.get("pack_hash"))
    comp = rep.get("completeness") or {}
    sections = rep.get("sections") or {}
    pipeline = rep.get("pipeline") or {}
    _step("no pipeline stage is left pending",
          all(s.get("status") != "pending" for s in pipeline.get("stages") or []))
    _step("no stage is recorded as unimplemented",
          all("not implemented" not in str(s.get("note") or "")
              for s in pipeline.get("stages") or []))
    _step("the study did not overrun its budget",
          0 < int(pipeline.get("solves_consumed", 0)) <= int(pipeline.get("budget_solves", 0)),
          f"solves_consumed={pipeline.get('solves_consumed')} "
          f"budget={pipeline.get('budget_solves')}")
    _step("target and cost are established", comp.get("target") == "ok"
          and comp.get("cost") == "ok", str({k: comp.get(k) for k in ("target", "cost")}))

    # WP1 — MC certify on the fixed plan.
    lole = rep.get("mc_lole_h")
    _step("mc_lole_h is finite", isinstance(lole, (int, float)) and math.isfinite(lole),
          f"mc_lole_h={lole}")
    cert = sections.get("certification") or {}
    _step("certification is ok", comp.get("certification") == "ok",
          f"status={comp.get('certification')} note={str(cert.get('note'))[:160]}")
    payload = cert.get("payload") or {}
    _step("the verdict is a decision-2 verdict",
          payload.get("verdict") in ("certified", "failed"),
          f"verdict={payload.get('verdict')}")
    _step("the verdict agrees with LOLE vs target",
          (payload.get("target_lole_h") is not None and lole is not None
           and ((payload["verdict"] == "certified")
                == (float(lole) <= float(payload["target_lole_h"]) + 1e-9))),
          f"lole={lole} target={payload.get('target_lole_h')}")
    _step("the MC charged no solves", _stage(rep, "mc_certify").get("solves_charged") == 0
          and _stage(rep, "mc_certify").get("status") == "run",
          str(_stage(rep, "mc_certify")))
    _step("the MC warning travels with the number", bool(payload.get("warning")))
    scope = payload.get("fleet_scope") or {}
    _step("the import is sampled, not counted firm",
          payload.get("import_model") in ("sampled_unit", "zonal")
          and payload.get("import_firmness") != "planning_limit_only",
          f"import_model={payload.get('import_model')} "
          f"firmness={payload.get('import_firmness')}")
    _step("the PoC Link is a sampled unit at the pack's 50 MW cap",
          [m.get("model") for m in scope.get("import_link_models") or []]
          == ["sampled_unit"]
          and scope.get("import_cap_mw_max") == 50.0
          and scope.get("import_firm_mw_max") is None,
          str(scope.get("import_link_models"))[:200])
    _step("the grid behind the Link is sampled as a second area",
          payload.get("import_model") == "zonal"
          and (scope.get("grid_area") or {}).get("units") == ["grid_supply"],
          str(scope.get("grid_area"))[:200])
    WEAK_EVIDENCE["mc_lole_h"] = lole
    WEAK_EVIDENCE["import_model"] = payload.get("import_model")

    # WP2 — frontier around the target.
    fr = sections.get("frontier") or {}
    pts = (fr.get("payload") or {}).get("points") or []
    ok_pts = [p for p in pts if p.get("status") == "ok" and p.get("point")]
    _step("frontier is ok", comp.get("frontier") == "ok",
          f"status={comp.get('frontier')} note={str(fr.get('note'))[:160]}")
    _step(f"frontier has ≥ {MIN_FRONTIER_POINTS} solved points",
          len(ok_pts) >= MIN_FRONTIER_POINTS, f"{len(ok_pts)} of {len(pts)}")
    _step("every frontier cost excludes shed and states its period basis",
          bool(pts) and all(p.get("excludes_shed_cost") is True for p in pts)
          and all(p.get("period_basis") in ("single_period", "multi_period")
                  for p in ok_pts),
          str([(p.get("excludes_shed_cost"), p.get("period_basis")) for p in pts])[:200])
    _step("the frontier passes through the report's own target",
          any(abs(float(p["target_permyriad"]) - float(rep["ens_cap_permyriad"])) < 1e-9
              for p in pts), f"targets={[p.get('target_permyriad') for p in pts]}")
    _step("the frontier's closing restore brought the plan back",
          (fr.get("payload") or {}).get("base_restored") is True,
          str((fr.get("payload") or {}).get("base_restore_status")))
    _step("frontier solves are charged (points + restore)",
          _stage(rep, "frontier").get("solves_charged") == len(pts) + 1,
          str(_stage(rep, "frontier")))

    # WP3 — FMEA top-N.
    fm = sections.get("fmea_top") or {}
    top = (fm.get("payload") or {}).get("top") or []
    _step("fmea_top is ok", comp.get("fmea_top") == "ok",
          f"status={comp.get('fmea_top')} note={str(fm.get('note'))[:160]}")
    _step("fmea_top is non-empty", bool(top), f"{len(top)} modes")
    crits = [float(m.get("criticality_eur_per_year") or 0.0) for m in top]
    _step("fmea_top is criticality-ranked", crits == sorted(crits, reverse=True))
    _step("fmea_top keeps the Link-primary Class-B note",
          "Link-primary" in str(fm.get("note")) and "SCLOPF" in str(fm.get("note")))
    _step("the import Link's outage is a ranked Class-B mode",
          any(m.get("failure_class") == "B" and m.get("component_class") == "Link"
              for m in top), str([(m.get("failure_class"), m.get("name")) for m in top]))
    _step("the sampled import Link is ranked once (its Class-B row)",
          sum(1 for m in top if m.get("name") in ("import_poc", "link:import_poc"))
          == 1 and (fm.get("payload") or {}).get("import_link_ranking")
          == {"import_poc": "class_b"},
          str((fm.get("payload") or {}).get("import_link_ranking")))

    # WP4 — LCOH flag on an electrical-only network.
    tea = rep.get("tea") or {}
    _step("TEA LCOE is established", comp.get("tea") == "ok"
          and tea.get("lcoe_eur_per_mwh") is not None)
    _step("LCOH is null with a flag (no electrolyser), never 0",
          tea.get("lcoh_eur_per_kg") is None and tea.get("lcoh_status") == "skipped",
          f"lcoh={tea.get('lcoh_eur_per_kg')} status={tea.get('lcoh_status')}")

    # The sibling tables the weak pack enables still arrive.
    _step("levers ran alongside the new stages",
          comp.get("levers") in ("ok", "not_established"),
          f"levers={comp.get('levers')}")
    _step("DtC stress ran alongside the new stages",
          comp.get("dtc") in ("ok", "not_established"),
          f"dtc={comp.get('dtc')}")


def section_2_electrolyser_lcoh() -> None:
    print("\n[2] strong_grid on a hub with an electrolyser — LCOH is finite")
    c = qa_support.client()
    qa_support.install_network(electrolyser_network())
    _configure(VOLL)
    study = _run_study("strong_grid", stages=["apply_pack", "ens_solve", "assemble"])
    if study.get("status") != "done":
        return
    r = c.get("/api/results/eh_reference_design")
    _step("the report is persisted for GET", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    rep = r.json()
    _step("the report is JSON-clean", not _nonfinite(rep), str(_nonfinite(rep)[:5]))
    tea = rep.get("tea") or {}
    lcoh = tea.get("lcoh_eur_per_kg")
    _step("LCOH is finite and positive with a consuming electrolyser",
          isinstance(lcoh, (int, float)) and math.isfinite(lcoh) and lcoh > 0,
          f"lcoh={lcoh} status={tea.get('lcoh_status')} note={str(tea.get('lcoh_note'))[:120]}")
    _step("LCOH is flagged ok", tea.get("lcoh_status") == "ok")
    comp = rep.get("completeness") or {}
    _step("stages not requested are skipped, not invented",
          comp.get("certification") == "skipped" and comp.get("frontier") == "skipped"
          and comp.get("fmea_top") == "skipped",
          str({k: comp.get(k) for k in ("certification", "frontier", "fmea_top")}))


def _certify(network, archetype: str) -> dict:
    """
    Install ``network``, run ``apply_pack → ens_solve → mc_certify →
    assemble`` and return the certification payload (+ ``mc_lole_h``).
    """
    c = qa_support.client()
    qa_support.install_network(network)
    _configure(VOLL)
    study = _run_study(archetype, stages=["apply_pack", "ens_solve",
                                          "mc_certify", "assemble"])
    if study.get("status") != "done":
        return {}
    r = c.get("/api/results/eh_reference_design")
    if r.status_code != 200:
        _step("the report is persisted for GET", False, f"HTTP {r.status_code}")
        return {}
    rep = r.json()
    _step(f"{archetype}: the report is JSON-clean", not _nonfinite(rep),
          str(_nonfinite(rep)[:5]))
    cert = ((rep.get("sections") or {}).get("certification") or {})
    return {**(cert.get("payload") or {}), "_mc_lole_h": rep.get("mc_lole_h"),
            "_status": (rep.get("completeness") or {}).get("certification")}


def section_3_import_outages() -> None:
    print("\n[3] the import Link sampled vs firm — and the islanded control")
    sampled_n, firm_n = firm_vs_sampled_import_pair()
    s = _certify(sampled_n, "weak_flexible")
    f = _certify(firm_n, "weak_flexible")
    _step("the Link with occurrence data is sampled (v1)",
          s.get("import_model") == "sampled_unit"
          and s.get("import_firmness") == "outage_sampled",
          f"{s.get('import_model')} / {s.get('import_firmness')}")
    _step("the Link without occurrence data is a firm block",
          f.get("import_model") == "firm_block"
          and f.get("import_firmness") == "planning_limit_only",
          f"{f.get('import_model')} / {f.get('import_firmness')}")
    ls, lf = s.get("_mc_lole_h"), f.get("_mc_lole_h")
    _step("a reliable Link with q > 0 RAISES the MC LOLE over the firm block",
          isinstance(ls, (int, float)) and isinstance(lf, (int, float))
          and ls > lf, f"sampled={ls} firm={lf}")
    WEAK_EVIDENCE["pair_sampled_lole_h"] = ls
    WEAK_EVIDENCE["pair_firm_lole_h"] = lf

    s_isl, f_isl = firm_vs_sampled_import_pair(peaker_mw=65.0)
    a = _certify(s_isl, "off_grid")
    b = _certify(f_isl, "off_grid")
    _step("off_grid islands the Link either way",
          a.get("import_model") == b.get("import_model") == "islanded",
          f"{a.get('import_model')} / {b.get('import_model')}")
    la, lb = a.get("_mc_lole_h"), b.get("_mc_lole_h")
    _step("the islanded hub's LOLE is unchanged by the Link's occurrence data",
          isinstance(la, (int, float)) and la == lb, f"{la} vs {lb}")
    WEAK_EVIDENCE["off_grid_lole_h"] = (la, lb)


def main() -> int:
    print("=" * 60)
    print("QA: the Energy Hub reference design (wired stages)")
    print("=" * 60)
    crashed = False
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        section_1_weak_flexible()
        section_2_electrolyser_lcoh()
        section_3_import_outages()
    except Exception as exc:                                     # noqa: BLE001
        crashed = True
        import traceback
        print(f"\n  [FAIL] the journey raised {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        try:
            qa_support.delete_project(PROJECT)
            qa_support.reset_backend()
        except Exception as exc:                                 # noqa: BLE001
            print(f"  ! cleanup raised {type(exc).__name__}: {exc}")

    if WEAK_EVIDENCE:
        print(f"\n  evidence: {WEAK_EVIDENCE}")
    total = PASS + FAIL + (1 if crashed else 0)
    print("\n" + "=" * 60)
    print(f"Total: {total}")
    print(f"Pass:  {PASS}")
    print(f"Fail:  {FAIL + (1 if crashed else 0)}")
    print("=" * 60)
    return 1 if (FAIL or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
