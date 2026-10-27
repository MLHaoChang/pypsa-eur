"""
U3 rule 5 through the routes (parent plan §3 rule 5): an Expert edit to an
option fork, saved to disk with S1a's `customised` stamp, shows on the
study's ledger as `customised` by `expert`, naming the option. The stored
ledger is not rewritten, and an untouched run shows nothing.

Runs a real study on the fake solver of `tests/test_study_runner.py` (the
queue, forks, campaign and readers are real; only `run_simulation` is
replaced), as the other route tests do.
"""
from __future__ import annotations

import pypsa
import pytest

from services.asset_schema import provenance
from services.pypsa_service import PyPSAService
from services.study import packs, store
from tests.study_s4_support import create_pack_study, enable_studies, wait_run
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import NO_PV_TOU, TOU_DC


def _study_money_year() -> int:
    from services.study import library as L

    return L.load_defaults().pack.finance.currency_year


# The user's tariff, stated in the study's money (the pinned pack's year), so
# the case does not mix it with the seeded rows (`currency_year_mixed`).
INTAKE = {**NO_PV_TOU, "tariff": {"custom": {**TOU_DC, "currency_year": _study_money_year()}}}


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _run(client, api_project, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name, intake=INTAKE)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, name, sid)["status"] == "done"
    return sid


def _edit_fork_on_disk(project_storage_dir, base: str, option: str, factor: float):
    """What a saved Expert edit leaves: a new value and S1a's `customised` stamp."""
    path = project_storage_dir(f"{base}-opt-{option}") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    n.storage_units.at[packs.BATTERY_NAME, "inv_power_overnight"] *= factor
    provenance.mark_customised(n, "StorageUnit", packs.BATTERY_NAME, ["inv_power_overnight"])
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, path)


def _row(body, key):
    return next(r for r in body["ledger"]["rows"] if r["key"] == key)


def test_an_untouched_run_shows_no_expert_edit(client, api_project, studies_on, fake):
    name = "r5-clean"
    sid = _run(client, api_project, name)
    body = client.get(f"/api/projects/{name}/studies/{sid}/ledger").json()
    assert not any(n.startswith("changed_in_expert:") for n in body["ledger"]["honesty_notes"])
    assert all(r.get("changed_by") != "expert" for r in body["ledger"]["rows"])


def test_an_expert_edit_on_a_fork_marks_its_ledger_row(client, api_project, studies_on, fake,
                                                       project_storage_dir):
    name = "r5-edit"
    sid = _run(client, api_project, name)
    before = client.get(f"/api/projects/{name}/studies/{sid}/ledger").json()
    _edit_fork_on_disk(project_storage_dir, name, "bess_2h", 1.25)

    body = client.get(f"/api/projects/{name}/studies/{sid}/ledger").json()
    row = _row(body, "battery_inverter_eur_per_kw")
    assert row["status"] == "customised" and row["changed_by"] == "expert"
    assert "changed_in_expert:battery_inverter_eur_per_kw:bess_2h" in body["ledger"]["honesty_notes"]
    # Only that row.
    assert [r["key"] for r in body["ledger"]["rows"] if r.get("changed_by") == "expert"] \
        == ["battery_inverter_eur_per_kw"]

    # A view: the stored ledger still holds the study's own row.
    stored = store.load_study(project_storage_dir(name), sid).ledger
    srow = next(r for r in stored.rows if r.key == "battery_inverter_eur_per_kw")
    assert srow.status == "default" and srow.changed_by is None
    assert srow.value == row["value"]
    # The study's maturity is its own inputs', not moved by the fork's edit.
    assert body["maturity"] == before["maturity"]
    # And the findings go stale: the run no longer matches its forks.
    f = client.get(f"/api/projects/{name}/studies/{sid}/findings")
    stale = f.status_code == 409 or "fork_changed_since_run" in f.text
    assert stale, (f.status_code, f.text[:300])
