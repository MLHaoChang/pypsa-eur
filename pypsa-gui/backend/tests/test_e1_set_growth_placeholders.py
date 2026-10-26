"""E1 follow-up: setting a growth placeholder (``services.growth_placeholders``).

A staged example built for entered planning years marks a growth value it has
no figure for as a placeholder (E1a/E1b/E1c). A user edit of the series the
row names, in the row's period, sets it. Gatekeeper B's plan verdict
(2026-10-26, OK with conditions):

C1. every user writer of ``_user_ts`` sets it (the time-series upload and PUT,
    the profile upload, ``assign_profile``, the chat load-profile tool);
C2. ``nh3_delivery_tpd`` = peak MWh_NH3/h × 24/5.166, round-tripped against the
    build's t/d → MWh/h;
C3. undo restores the row;
C4. the metadata is written atomically in the same request;
C5. per period: a write to another period does not set the row;
Q1 (a). ``load_scale`` is set only once every load it names is written;
Q2 (a). the metadata is written at the edit.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from services import growth_placeholders as GP  # noqa: E402

DC = "eh_datacenter_staged"
DC_SHIFTED = {"start_year": 2027, "end_year": 2045, "dates": [2030, 2038]}
#: The staged hubs at 2029 / 2032 / 2037: no year is the example's, every growth value a placeholder.
HUB_SHIFTED = {"start_year": 2029, "end_year": 2047}


# ── the kinds and the units ─────────────────────────────────────────

def test_the_kinds_are_the_templates_growth_kinds():
    plural = {"Load": "loads", "Link": "links"}
    assert GP.KINDS == {k: (plural[c], entity, field)
                        for k, (_l, _u, _s, c, entity, field) in T._GROWTH_KINDS.items()}


def test_nh3_delivery_round_trips_the_builds_tpd_to_mwh_per_h():
    """C2: the build writes t/d × 5.166/24 as MWh_NH3/h; setting reads it back."""
    from project_templates import eh_ammonia as A

    assert GP.MWH_PER_T == A.MWH_PER_T
    for tpd in (500.0, 1200.0, 1667.5):
        assert GP._value_of("nh3_delivery_tpd", A._tpd_mw(tpd), None, 0.0) == pytest.approx(tpd)


def test_a_load_value_moves_with_its_peak_and_the_energization_is_mw():
    assert GP._value_of("it_mw", 44.0, 40.0, 30.0) == pytest.approx(33.0)
    assert GP._value_of("load_scale", 10.0, 5.0, 1.5) == pytest.approx(3.0)
    assert GP._value_of("energized_mw", 55.0, 40.0, 30.0) == 55.0
    assert GP._value_of("it_mw", 44.0, 0.0, 30.0) is None


# ── through the writers ─────────────────────────────────────────────

def _create(client, tid, name, planning):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name},
                    json={"planning": planning})
    assert r.status_code == 200, r.text


def _dir(bound_ctx, client) -> pathlib.Path:
    from services import network_lengths

    with bound_ctx(client):
        return network_lengths.active_project_dir()


def _meta(d) -> dict:
    return json.loads((d / T.META_FILE).read_text())


def _row(meta, kind, year) -> dict:
    return next(g for g in meta["growth_inputs"] if g["kind"] == kind and g["year"] == year)


def _ledger(meta, kind, year) -> dict | None:
    return next((e for e in meta["ledger"] if e.get("item") == f"placeholder {kind} {year}"), None)


def _series(bound_ctx, client, component, attribute, col, period):
    from services.pypsa_service import PyPSAService

    with bound_ctx(client):
        n = PyPSAService.get_network()
        return getattr(n, f"{component}_t")[attribute][col].xs(period, level=0).copy()


def _upload(client, component, attribute, frame, period=None):
    params = {"component": component, "attribute": attribute}
    if period is not None:
        params["period"] = period
    return client.post("/api/network/timeseries/upload", params=params,
                       files={"file": ("edit.csv", frame.to_csv(), "text/csv")})


def test_an_upload_for_the_period_sets_it_mw_and_its_flag_leaves(
        client, tmp_projects_dir, bound_ctx):
    from routers import results as R

    _create(client, DC, "gp_it", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    row = _row(_meta(d), "it_mw", 2038)
    assert row["set"] is False
    s = _series(bound_ctx, client, "loads", "p_set", "it_load", 2038)
    r = _upload(client, "loads", "p_set", (s * 1.25).to_frame("it_load"), period=2038)
    assert r.status_code == 200, r.text
    meta = _meta(d)
    row2 = _row(meta, "it_mw", 2038)
    assert row2["set"] is True and row2["source"] == "entered"
    assert row2["value"] == pytest.approx(row["value"] * 1.25)
    e = _ledger(meta, "it_mw", 2038)
    assert e["source"] == "entered" and "(placeholder)" not in e["label"]
    with bound_ctx(client):
        flags = R._ic_growth_flags()
    assert "growth_placeholder:it_mw_2038" not in flags
    assert "growth_placeholder:energized_mw_2038" in flags
    # The flow no longer warns on it.
    body = client.get("/api/projects/gp_it/flow").json()
    codes = {i["code"] for s_ in body["sections"] for i in s_["items"]}
    assert "assumed_placeholder it_mw 2038" not in codes
    assert "assumed_placeholder energized_mw 2038" in codes


def test_a_write_to_another_period_leaves_the_placeholder(client, tmp_projects_dir, bound_ctx):
    """C5."""
    _create(client, DC, "gp_c5", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    s = _series(bound_ctx, client, "loads", "p_set", "it_load", 2030)
    assert _upload(client, "loads", "p_set", (s * 1.1).to_frame("it_load"),
                   period=2030).status_code == 200
    assert _row(_meta(d), "it_mw", 2038)["set"] is False


def test_an_edit_back_to_the_placeholder_value_sets_it(client, tmp_projects_dir, bound_ctx):
    _create(client, DC, "gp_same", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    before = _row(_meta(d), "it_mw", 2038)
    s = _series(bound_ctx, client, "loads", "p_set", "it_load", 2038)
    assert _upload(client, "loads", "p_set", s.to_frame("it_load"), period=2038).status_code == 200
    row = _row(_meta(d), "it_mw", 2038)
    assert row["set"] is True and row["value"] == pytest.approx(before["value"])


def test_the_energization_edit_gives_the_period_a_verdict(client, tmp_projects_dir, bound_ctx):
    import pypsa

    from services.adequacy import grid_readiness as G
    from services.pypsa_service import PyPSAService

    _create(client, DC, "gp_en", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    s = _series(bound_ctx, client, "links", "eh_energization_mw", "grid_import", 2038)
    s[:] = 60.0
    assert _upload(client, "links", "eh_energization_mw", s.to_frame("grid_import"),
                   period=2038).status_code == 200
    row = _row(_meta(d), "energized_mw", 2038)
    assert row["set"] is True and row["value"] == 60.0
    # The peak check also needs it_mw 2038; set it at its own value.
    s = _series(bound_ctx, client, "loads", "p_set", "it_load", 2038)
    assert _upload(client, "loads", "p_set", s.to_frame("it_load"), period=2038).status_code == 200
    with bound_ctx(client):
        n = PyPSAService.get_network()
        rows = {r["id"]: r for r in G.grid_readiness(d, n, "eu_rfg_dcc_ce")["rows"]}
    assert rows["energization_peak:2038"]["verdict"] != "unknown"
    assert isinstance(n, pypsa.Network)


def test_undo_puts_the_placeholder_back(client, tmp_projects_dir, bound_ctx):
    """C3: the undone edit does not stay entered."""
    _create(client, DC, "gp_undo", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    s = _series(bound_ctx, client, "loads", "p_set", "it_load", 2038)
    assert _upload(client, "loads", "p_set", (s * 1.2).to_frame("it_load"),
                   period=2038).status_code == 200
    assert _row(_meta(d), "it_mw", 2038)["set"] is True
    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text
    assert _row(_meta(d), "it_mw", 2038)["set"] is False
    assert _ledger(_meta(d), "it_mw", 2038)["source"] == "assumed"


def test_a_put_of_the_series_sets_every_period_it_covers(client, tmp_projects_dir, bound_ctx):
    """The PUT writes the whole column: every period it carries."""
    _create(client, DC, "gp_put", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    from services.pypsa_service import PyPSAService

    with bound_ctx(client):
        s = PyPSAService.get_network().loads_t.p_set["it_load"].copy()
    m = _meta(d)
    unset = [g["year"] for g in m["growth_inputs"] if g["kind"] == "it_mw" and not g["set"]]
    assert unset
    flat = s.xs(unset[0], level=0)
    r = client.put("/api/network/timeseries/loads/p_set", json={
        "index": [t.isoformat() for t in flat.index], "columns": ["it_load"],
        "data": [[float(v)] for v in flat.to_numpy()]})
    assert r.status_code == 200, r.text
    for y in unset:
        assert _row(_meta(d), "it_mw", y)["set"] is True


def test_the_profile_upload_sets_h2_offtake_in_every_period_it_broadcasts_to(
        client, tmp_projects_dir, bound_ctx):
    """A flat profile under a multi-period axis is the same week in every
    period, so it sets each period, each in its own value (C5)."""
    _create(client, "eh_h2_hub_staged", "gp_h2", HUB_SHIFTED)
    d = _dir(bound_ctx, client)
    y = _row(_meta(d), "h2_offtake_mw", 2032)
    assert y["set"] is False
    s = _series(bound_ctx, client, "loads", "p_set", "h2_offtake", 2032)
    r = client.post("/api/network/loads/upload_profile",
                    files={"file": ("p.csv", (s * 2.0).to_frame("h2_offtake").to_csv(), "text/csv")})
    assert r.status_code == 200, r.text
    meta = _meta(d)
    assert _row(meta, "h2_offtake_mw", 2032)["value"] == pytest.approx(y["value"] * 2.0)
    assert all(g["set"] for g in meta["growth_inputs"] if g["kind"] == "h2_offtake_mw")


def test_load_scale_is_set_only_once_every_load_is_written(client, tmp_projects_dir, bound_ctx):
    """Q1 (a): one load's edit out of several keeps the period a placeholder."""
    _create(client, "eh_microgrid_staged", "gp_mg", HUB_SHIFTED)
    d = _dir(bound_ctx, client)
    row = _row(_meta(d), "load_scale", 2032)
    loads = list(row["value"])
    assert row["set"] is False and len(loads) > 1
    for i, load in enumerate(loads):
        s = _series(bound_ctx, client, "loads", "p_set", load, 2032)
        assert _upload(client, "loads", "p_set", (s * 1.5).to_frame(load),
                       period=2032).status_code == 200
        row2 = _row(_meta(d), "load_scale", 2032)
        assert row2["value"][load] == pytest.approx(row["value"][load] * 1.5)
        assert row2["set"] is (i == len(loads) - 1)


def test_the_ammonia_delivery_is_set_in_t_per_day(client, tmp_projects_dir, bound_ctx):
    from project_templates import eh_ammonia as A

    _create(client, "eh_ammonia_staged", "gp_nh3", HUB_SHIFTED)
    d = _dir(bound_ctx, client)
    assert _row(_meta(d), "nh3_delivery_tpd", 2032)["set"] is False
    s = _series(bound_ctx, client, "loads", "p_set", "nh3_delivery", 2032)
    s[:] = A._tpd_mw(900.0)
    assert _upload(client, "loads", "p_set", s.to_frame("nh3_delivery"),
                   period=2032).status_code == 200
    row = _row(_meta(d), "nh3_delivery_tpd", 2032)
    assert row["set"] is True and row["value"] == pytest.approx(900.0)


def test_assign_profile_sets_the_load(client, tmp_projects_dir, bound_ctx):
    """C1: the path behind the profiles apply route and its chat tools."""
    import pandas as pd

    from services.profile_gen.apply import assign_profile
    from services.pypsa_service import PyPSAService

    _create(client, DC, "gp_assign", DC_SHIFTED)
    d = _dir(bound_ctx, client)
    before = _row(_meta(d), "it_mw", 2038)
    week = _series(bound_ctx, client, "loads", "p_set", "it_load", 2038)
    peak = float(week.max())
    # An hourly year (per unit of its peak) whose study week is 2038's shape:
    # sliced to the week, tiled over the periods.
    year = pd.Series(0.5, index=pd.date_range(f"{week.index[0].year}-01-01", periods=8760, freq="h"))
    year.loc[week.index] = (week / peak).to_numpy()
    with bound_ctx(client):
        assign_profile(PyPSAService.get_active_context(), "it_load", year,
                       {"family": "load", "source": "archetype", "archetype": "test",
                        "unit": "per_unit_peak", "scale_mw": 1.5 * peak, "resolution": "hourly",
                        "generated_at": "2026-10-26T20:00:00+00:00"},
                       component_type="loads", attribute="p_set",
                       now="2026-10-26T20:00:00+00:00")
    row = _row(_meta(d), "it_mw", 2038)
    assert row["set"] is True and row["value"] == pytest.approx(before["value"] * 1.5)


def test_without_a_template_file_a_write_is_untouched(client, tmp_projects_dir, bound_ctx):
    """A project with no ``eh_template.json`` (the common case): nothing to do."""
    from services.pypsa_service import PyPSAService

    with bound_ctx(client):
        n = PyPSAService.get_network()
        assert GP.before(n, "loads", "p_set", ["x"], project_dir=tmp_projects_dir) is None
        assert GP.mark_set(n, "loads", "p_set", {"x": None}, None) == []
