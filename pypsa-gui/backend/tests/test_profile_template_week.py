"""G-a/RP-12: real template windows use the weather year's own calendar dates."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import queue
import threading
import time
import uuid

import numpy as np
import pandas as pd
import pytest
import xarray as xr


def _template(client, tid):
    res = client.post(
        f"/api/projects/from_template/{tid}", params={"name": f"Weather {tid}"}
    )
    assert res.status_code == 200, res.text
    return res.json()["imported"]


def _generate_apply(client, name, ctx, generator, technology):
    from services import solve_queue as sq

    res = client.post(
        f"/api/projects/{name}/profiles/generate",
        json={
            "from_generators": [generator],
            "technology": technology,
            "source": "synthetic",
        },
    )
    assert res.status_code == 202, res.text
    jid = res.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(jid))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    record = next(
        r
        for r in client.get(f"/api/projects/{name}/profiles/pending").json()["pending"]
        if r["job_id"] == jid
    )
    assert record["year_source"] == "default"
    assert record["weather_year"] == dt.datetime.now(dt.UTC).year - 1
    with xr.open_dataset(
        Path(ctx.storage_dir) / "profiles_pending" / f"{jid}.nc"
    ) as ds:
        source = ds[generator].to_series()
    receipt = client.post(
        f"/api/projects/{name}/profiles/apply",
        json={"job_id": jid, "component": generator},
    )
    assert receipt.status_code == 200, receipt.text
    assert "before" in receipt.json()
    assert receipt.json()["window"]["hours"] == 168
    stored = ctx.user_ts[("generators", "p_max_pu", generator)]
    stamps = (
        stored.index.get_level_values(-1)
        if isinstance(stored.index, pd.MultiIndex)
        else stored.index
    )
    keys = pd.DatetimeIndex([t.replace(year=record["weather_year"]) for t in stamps])
    np.testing.assert_array_equal(stored.to_numpy(), source.loc[keys].to_numpy())
    assert len(stored) == 168


def test_the_weather_answer_names_the_window(client, session_ctx):
    name = _template(client, "eh_h2_hub")
    got = client.get(f"/api/projects/{name}/profiles/weather").json()
    assert got["window"] == {
        "first": "2030-07-01 00:00:00",
        "last": "2030-07-07 23:00:00",
        "hours": 168,
        "runs": 1,
    }
    ctx = session_ctx(client)
    ctx.network.set_snapshots(pd.date_range("2023-01-01", periods=8760, freq="h"))
    assert client.get(f"/api/projects/{name}/profiles/weather").json()["window"] is None
    ctx.network.set_snapshots(pd.date_range("2030-07-01 00:30", periods=168, freq="h"))
    unsupported = client.get(f"/api/projects/{name}/profiles/weather").json()["window"]
    assert (
        isinstance(unsupported["unsupported"], str)
        and "hour" in unsupported["unsupported"]
    )


@pytest.mark.parametrize(
    "tid,bus,assets",
    [
        ("eh_h2_hub", "hub", [("wind_farm", "wind"), ("solar_park", "solar")]),
        ("eh_microgrid", "island", [("wind_turbines", "wind"), ("pv_plant", "solar")]),
    ],
)
def test_a_template_week_takes_the_weather_years_own_dates(
    client, session_ctx, tid, bus, assets
):
    name = _template(client, tid)
    ctx = session_ctx(client)
    weights = ctx.network.snapshot_weightings.copy(deep=True)
    ctx.network.buses.loc[bus, ["x", "y"]] = [8.0, 54.0]
    for generator, technology in assets:
        _generate_apply(client, name, ctx, generator, technology)
    pd.testing.assert_frame_equal(ctx.network.snapshot_weightings, weights)


@pytest.mark.live_solve
def test_the_template_still_solves_after_apply(client, session_ctx, bound_ctx):
    from project_templates import eh_templates as templates
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    name = _template(client, "eh_h2_hub")
    ctx = session_ctx(client)
    ctx.network.buses.loc["hub", ["x", "y"]] = [8.0, 54.0]
    _generate_apply(client, name, ctx, "wind_farm", "wind")
    _generate_apply(client, name, ctx, "solar_park", "solar")
    config = _solver_config_from_dict(templates.solver_config("eh_h2_hub"))
    with bound_ctx(client):
        status, condition = run_simulation(
            config,
            ctx.network,
            PyPSAService.get_lock(),
            threading.Event(),
            queue.SimpleQueue(),
        )
    assert status in ("ok", "optimal"), (status, condition)
