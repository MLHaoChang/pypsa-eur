"""Generate defaults for all (gatekeeper plan D, 2026-10-20).

* Each load gets an archetype guessed from its name, else its bus carrier's
  default, marked assumed with the reason; an unknown carrier, a zero p_set,
  a measured upload and (unless asked) a generated series are skipped, each
  saying why.
* Sizing is mean-preserving on the model's own weighted snapshots: the
  energy stays as the model has it; peak sizing is a per-row option. A peak
  above the point of connection is flagged.
* Apply-all writes under one undo step, re-checks "never over measured" per
  row, and reports each row; a refusal does not roll the others back.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

YEAR = pd.date_range("2025-01-01", periods=8760, freq="h")


def _network(snapshots=YEAR, weights=None):
    n = pypsa.Network()
    idx = snapshots.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    if weights is not None:
        n.snapshot_weightings.loc[:, :] = weights
    n.add("Bus", "B1", carrier="AC")
    n.add("Bus", "H", carrier="heat")
    n.add("Bus", "X", carrier="hydrogen")
    n.add("Bus", "grid", carrier="AC")
    n.add("Link", "poc", bus0="grid", bus1="B1", p_nom=3.0)
    n.add("Load", "office", bus="B1", p_set=2.0)
    n.add("Load", "ev_hub", bus="B1", p_set=0.5)
    n.add("Load", "misc", bus="B1", p_set=1.0)
    n.add("Load", "space", bus="H", p_set=3.0)
    n.add("Load", "electrolyser_feed", bus="X", p_set=4.0)
    n.add("Load", "spare", bus="B1", p_set=0.0)
    n.add("Load", "metered", bus="B1", p_set=1.0)
    return n


@pytest.fixture
def site(client, install_network, session_ctx):
    install_network(_network())
    assert client.post("/api/projects/Defaults Site",
                       params={"force": True, "rebind": True}).status_code == 200
    ctx = session_ctx(client)
    ctx.user_ts[("loads", "p_set", "metered")] = pd.Series(1.0, index=ctx.network.snapshots)
    state = ctx.solver_state.setdefault("solver_config", SimpleNamespace())
    state.commercial = {"poc_link": "poc"}
    return "Defaults Site"


def _preview(client, project, **body):
    resp = client.post(f"/api/projects/{project}/profiles/defaults/preview", json=body)
    assert resp.status_code == 200, resp.text
    return {r["component"]: r for r in resp.json()["rows"]}


def test_each_row_says_what_it_guessed_and_why(client, site):
    rows = _preview(client, site)
    assert rows["office"]["kind"] == "commercial"
    assert "matched 'office' in the load name" in rows["office"]["reason"]
    assert rows["ev_hub"]["kind"] == "ev_charging"
    assert rows["space"]["kind"] == "heat"            # the heat bus's default
    assert "no match in the name 'space'" in rows["space"]["reason"]
    assert rows["misc"]["kind"] == "commercial"
    assert "no match in the name 'misc'" in rows["misc"]["reason"]
    assert all(r["assumed"] for r in rows.values() if "job_id" in r)
    assert "hydrogen" in rows["electrolyser_feed"]["skipped"]
    assert "static p_set is 0" in rows["spare"]["skipped"]
    assert "measured upload" in rows["metered"]["skipped"]
    assert rows["office"]["week"]["label"] == "peak week"


def test_defaults_keep_the_models_energy_and_apply_under_one_undo(client, site, session_ctx):
    rows = _preview(client, site)
    jobs = [r["job_id"] for r in rows.values() if "job_id" in r]
    resp = client.post(f"/api/projects/{site}/profiles/defaults/apply", json={"job_ids": jobs})
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == len(jobs)
    n = session_ctx(client).network
    for load, p_set in (("office", 2.0), ("ev_hub", 0.5), ("space", 3.0), ("misc", 1.0)):
        assert float(n.loads_t.p_set[load].mean()) == pytest.approx(p_set, rel=1e-6)
    assert client.post("/api/network/undo").status_code == 200   # one step for the batch
    n = session_ctx(client).network
    assert not {"office", "ev_hub", "space", "misc"} & set(n.loads_t.p_set.columns)


def test_a_peak_sized_row_and_the_connection_flag(client, site):
    rows = _preview(client, site, peak_sized=["office"])
    assert rows["office"]["sizing"] == "peak"
    assert rows["office"]["stats"]["peak_mw"] == pytest.approx(2.0)
    mean_rows = _preview(client, site)
    assert mean_rows["office"]["stats"]["mean_mw"] == pytest.approx(2.0)   # as the model sees it
    # Mean-preserving 2 MW of an office shape peaks above the 3 MW connection.
    assert mean_rows["office"]["stats"]["peak_mw"] > 3.0
    assert any(w.startswith("default_peak_above_connection") for w in mean_rows["office"]["warnings"])


def test_a_measured_upload_since_the_preview_is_kept(client, site, session_ctx):
    rows = _preview(client, site)
    ctx = session_ctx(client)
    ctx.user_ts[("loads", "p_set", "office")] = pd.Series(7.0, index=ctx.network.snapshots)
    jobs = [rows["office"]["job_id"], rows["misc"]["job_id"]]
    out = client.post(f"/api/projects/{site}/profiles/defaults/apply",
                      json={"job_ids": jobs}).json()
    by = {r["component"]: r for r in out["results"]}
    assert by["office"]["applied"] is False and by["office"]["error_kind"] == "measured_series_kept"
    assert by["misc"]["applied"] is True
    assert float(session_ctx(client).network.loads_t.p_set["office"].iloc[0]) == 7.0


def test_a_generated_series_is_skipped_unless_asked(client, site):
    rows = _preview(client, site)
    client.post(f"/api/projects/{site}/profiles/defaults/apply",
                json={"job_ids": [rows["misc"]["job_id"]]})
    assert "already has a generated series" in _preview(client, site)["misc"]["skipped"]
    assert "job_id" in _preview(client, site, replace_generated=True)["misc"]


def test_on_a_weighted_week_the_weighted_mean_is_the_static_p_set(client, install_network,
                                                                  session_ctx):
    week = pd.date_range("2025-06-02", periods=168, freq="h")
    install_network(_network(week, weights=8760 / 168))
    assert client.post("/api/projects/Week Defaults",
                       params={"force": True, "rebind": True}).status_code == 200
    rows = _preview(client, "Week Defaults")
    client.post("/api/projects/Week Defaults/profiles/defaults/apply",
                json={"job_ids": [rows["office"]["job_id"]]})
    p = session_ctx(client).network.loads_t.p_set["office"].to_numpy()
    assert float(np.mean(p)) == pytest.approx(2.0, rel=1e-6)


def test_the_assistant_previews_the_batch_on_one_card_and_applies_it(client, site, bound_ctx):
    from harness import edit_card
    from services import chat_tools

    # A chat turn runs its tools with the session's context bound.
    with bound_ctx(client):
        res = chat_tools.DISPATCHERS["preview_default_series"](project_id=site)
        rows = {r["component"]: r for r in res["rows"]}
        jobs = [rows["office"]["job_id"], rows["misc"]["job_id"]]
        args = {"project_id": site, "job_ids": jobs}
        card = edit_card.preview("apply_default_series", args)
        assert {r["name"] for r in card["rows"]} == {"office", "misc"}
        notes = [r["note"] for r in card["rows"] if r["field"] == "default"]
        assert any("matched 'office'" in n and "sized by its mean" in n for n in notes)
        out = chat_tools.DISPATCHERS["apply_default_series"](**args)
        assert out["applied"] == 2
        back = edit_card.ADAPTERS["apply_default_series"].readback(args, card["rows"])
    typed = [(r, v) for r, v in zip(card["rows"], back) if r["role"] == "typed"]
    assert typed and all(v == pytest.approx(r["new"], rel=1e-6) for r, v in typed)


def test_the_defaults_tools_say_only_the_type_of_a_foreign_error(client, site, bound_ctx,
                                                                monkeypatch):
    # The sanitiser's rule (#508) on the UI's defaults route and chat tools.
    from fastapi import HTTPException

    from services import chat_tools
    from services.profile_gen import defaults

    foreign = "CDSAPI_KEY=abc123 at /srv/secret/path"

    def boom(*a, **k):
        raise ValueError(foreign)

    monkeypatch.setattr(defaults, "preview_defaults", boom)
    with bound_ctx(client), pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["preview_default_series"](project_id=site)
    assert err.value.detail == "unexpected error (ValueError); see the server log"
