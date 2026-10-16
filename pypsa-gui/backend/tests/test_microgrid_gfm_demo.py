"""The staged microgrid's GFM demo: a recommendation that can be applied
(owner, GFM demo tuning; gatekeeper's plan review).

The microgrid's island fails I6 at its current settings. With its demo limit
(2 Hz/s over 500 ms, relaxed from the data center's 1 Hz/s and ledgered), the
hub sized for the island's own events (the seamless event rows, I2b) and the
search's cap of 1500 simulations, the real recommender finishes with
``recommended`` at the Guided default ranking (k=3, 136 cases). Applying it writes the settings into the sidecar and the
campus file; the recommendation then reads stale, the hub must be re-solved
(the microgrid ships its island rows on), and a second apply is refused.

The campus run is the route's own steps up to the frequency check (prepare,
rank, the island static checks, I6); I7's cuts, which the recommender does
not read, are left out (they take about half an hour on this island).
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import project_registry
from tests.test_campus_island_cuts_e2e import user_and_db  # noqa: F401
from tests.test_eh_staged_hubs import H, T, _solve

TID = "eh_microgrid_staged"


def _frequency_check(row):
    """The campus run's steps up to I6 (``ce.run``'s prepare, rank and
    island checks without I7), at the Guided and Expert default ranking
    (``ce.DEFAULTS["k"]``, 3 critical hours per criterion; Guided smoke
    2026-10-16: at k=1 the ranking left out the hours that failed)."""
    spec = yaml.safe_load((ce.campus_dir(row) / ce.CAMPUS_FILE).read_text())
    run = ce.campus_dir(row) / "run"
    ce.cs.prepare_campus(run, spec, ce._network(row))
    ce._write_island_tables(row, run, spec, ce._network(row))
    ce.cs.rank_campus(run, k=ce.DEFAULTS["k"])
    ce.cs.island_static_campus(run)
    ce.cs.island_frequency_campus(run)
    return run


def test_the_demo_runs_at_the_guided_default_ranking():
    assert ce.DEFAULTS["k"] == 3


def test_the_demo_limit_is_ledgered_and_only_the_transition_is_relaxed():
    lim = T.ISLAND_CONFIG[TID]["requirements"]["frequency_limits"]
    assert lim["transition"]["rocof_hz_per_s"] == {"value": H.MG_TRANSITION_ROCOF, "source": "assumed"}
    assert lim["steady_island"] == T._LIMITS
    assert {k: v for k, v in lim["transition"].items() if k != "rocof_hz_per_s"} == \
        {k: v for k, v in T._LIMITS.items() if k != "rocof_hz_per_s"}
    meta = T.TEMPLATE_META[TID]
    row = next(r for r in meta["ledger"] if r["item"] == "transition RoCoF limit")
    assert "demo value" in row["note"] and "2 Hz/s" in row["value"]
    assert any("2 Hz/s" in s for s in meta["study_notes"])


@pytest.mark.live_solve
@pytest.mark.slow
def test_the_microgrid_gets_a_recommendation_it_can_apply(user_and_db, reset_backend,  # noqa: F811
                                                          monkeypatch, tmp_path):
    from services.campus_island_report import island_report

    n, _cfg, _state = _solve(TID, monkeypatch, tmp_path)
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Microgrid GFM demo")
    d = project_registry.ensure_project_dir(row)
    n.export_to_netcdf(str(d / "network.nc"))
    T.write_sidecars(d, TID)
    ce.draft(row)
    run = _frequency_check(row)

    # the relaxed limit is shown where the limits are: the campus file's
    # parameters, the report's requirements table and the GFM panel's ledger
    campus = ce.cs.load_run_campus(run)
    p = campus.params
    assert float(p.loc[(p.element == "island.transition") & (p.param == "rocof_hz_per_s"), "value"].iloc[0]) \
        == H.MG_TRANSITION_ROCOF
    req = next(t for t in island_report(row)["tables"] if t["id"] == "requirements")
    assert ["transition.rocof_hz_per_s", H.MG_TRANSITION_ROCOF, "assumed"] in req["rows"]

    cit.recommend(run, max_sims=cit.MAX_SIMS)
    got = cit.get_settings(row)
    assert got["state"] == "ready" and got["applied"] is None
    rec = got["recommendation"]
    assert rec["status"] == "recommended", rec["notes"]
    assert rec["simulations"] <= cit.MAX_SIMS
    assert all(c["ok_after"] for c in rec["cases"]) and not all(c["ok_before"] for c in rec["cases"])
    assert any("transition limits: RoCoF 2 Hz/s over 500 ms" in line for line in rec["ledger"])

    out = cit.apply_settings(row)
    assert out["hub_resolve_needed"] is True                 # the microgrid's island rows are on
    sidecar = json.loads((d / "island_config.json").read_text())
    by_uid = {u["pypsa_name"]: uid for uid, u in yaml.safe_load(
        (ce.campus_dir(row) / ce.CAMPUS_FILE).read_text())["campus"]["units"].items() if "pypsa_name" in u}
    changed = {(c["pypsa_name"], c["field"]): c["to"] for c in out["changes"]}
    assert changed
    for (name, field), to in changed.items():
        assert sidecar["units"][name][field] == {"value": to, "source": "assumed"}
        s = rec["settings"][by_uid[name]]["recommended"]
        assert to == pytest.approx(s[field])
    # the recommendation is now the old run's, and the hub must be re-solved
    after = cit.get_settings(row)
    assert after["state"] == "stale" and after["applied"]["hub_resolve_needed"] is True
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(row)
    assert exc.value.status_code == 409


def test_the_panel_ledger_names_the_limits_the_search_judged_against(tmp_path):
    a = lambda v: {"value": v, "source": "assumed"}  # noqa: E731
    island = {"rocof_window_ms": a(500.0), "frequency_limits": {
        "transition": {"rocof_hz_per_s": a(2.0), "f_min_hz": a(47.5), "f_max_hz": a(51.5),
                       "qss_band_hz": a(1.0)},
        "steady_island": {"rocof_hz_per_s": {"value": 1.0, "source": "datasheet"}}}}
    (tmp_path / ce.cs.CAMPUS_YAML).write_text(yaml.safe_dump({"campus": {"island": island}}))
    assert cit._limit_lines(tmp_path) == [
        "judged against the steady island limits: RoCoF 1 Hz/s over 500 ms (datasheet)",
        "judged against the transition limits: RoCoF 2 Hz/s over 500 ms (assumed), f min 47.5 Hz "
        "(assumed), f max 51.5 Hz (assumed), quasi-steady band 1 Hz (assumed)"]
    (tmp_path / ce.cs.CAMPUS_YAML).write_text("campus: {}\n")
    assert cit._limit_lines(tmp_path) == []


@pytest.mark.parametrize("tid,transition", [("eh_datacenter", 1.0), ("eh_datacenter_staged", 1.0),
                                            (TID, H.MG_TRANSITION_ROCOF)])
def test_the_ledger_line_reads_each_templates_own_limits(tid, transition, tmp_path):
    """``_state`` serves every project: the line is drafted from each
    template's own sidecar, never another's or a default."""
    from gridspine.producers.campus import draft_campus
    from services.adequacy import island_config as IC

    n = T.BUILDERS[tid]()
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        df["p_nom_opt"] = df["p_nom"].where(df["p_nom"] > 0, 10.0)
    spec = draft_campus(n, island=IC.validate(T.ISLAND_CONFIG[tid])).spec
    (tmp_path / ce.cs.CAMPUS_YAML).write_text(yaml.safe_dump(spec, sort_keys=False))
    lines = cit._limit_lines(tmp_path)
    assert lines[0].startswith("judged against the steady island limits: RoCoF 1 Hz/s over 500 ms")
    assert lines[1].startswith(f"judged against the transition limits: RoCoF {transition:g} Hz/s over 500 ms")


@pytest.mark.parametrize("limits,window,expected", [
    ({"t": {"rocof_hz_per_s": {"value": "2"}}}, {"value": 500.0}, []),            # a string value
    ({"t": {"rocof_hz_per_s": {"value": None}, "f_min_hz": {"value": 47.5}}},       # a null value: the
     {"value": 500.0}, ["judged against the t limits: f min 47.5 Hz (assumed)"]),   # field is left out
    (["x"], {"value": 500.0}, []),                                                  # not a mapping
    ({"t": {"rocof_hz_per_s": {"value": 2.0}}}, {"value": "500"},                  # a bad window: no "over"
     ["judged against the t limits: RoCoF 2 Hz/s (assumed)"]),
    ({"t": ["x"], "u": {"rocof_hz_per_s": {"value": True}}}, None, []),            # a bool is not a number
])
def test_a_malformed_limit_never_fails_the_panel(limits, window, expected, tmp_path):
    island = {"frequency_limits": limits}
    if window is not None:
        island["rocof_window_ms"] = window
    (tmp_path / ce.cs.CAMPUS_YAML).write_text(yaml.safe_dump({"campus": {"island": island}}))
    assert cit._limit_lines(tmp_path) == expected
