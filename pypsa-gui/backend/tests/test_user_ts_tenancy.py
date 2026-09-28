"""
Tenancy of the user-uploaded time-series store.

WHAT THESE PIN. `services/user_timeseries.py` holds every GUI-uploaded profile
keyed ``(component, attribute, column)``. That store is authoritative, not a
cache: ``GET /api/network/timeseries/{component}/{attribute}`` PREFERS it over
the network's own ``_t`` tables, every foreground save serialises it into the
project's ``user_ts.json``, and ``_reapply_user_ts_to_network`` writes it back
onto the network right before the netCDF export — so whatever is in it at save
time is what lands in ``network.nc`` and in the solve results.

While the store was one process-level dict, all of that was shared by every
signed-in session on the process. Reproduced and written up in
``docs/superpowers/findings/2026-09-12-user-ts-is-a-process-global-shared-across-tenants.md``:
org A uploaded a load profile and org B, in a DIFFERENT organization, read A's
numbers back from its OWN project and then persisted them into its own
``user_ts.json`` / ``network.nc``.

The four tests below are the four properties that finding's "what a fix has to
establish" section names, asserted DIRECTLY — a second tenant reading the first
tenant's column, a save carrying a foreign column, an activation discarding
someone else's in-flight upload, and two contexts sharing one dict. They are
deliberately NOT written as "the one path the reproduction used is blocked":
a test shaped like that goes green on a containment that leaves the class open.
"""
from __future__ import annotations

import io
import json

import pandas as pd
import pypsa
import pytest

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService


SNAPS = pd.date_range("2025-01-01", periods=4, freq="h")
STATIC_P_SET = 100.0
UPLOADED_P_SET = 777.0


def _net_with_one_load() -> pypsa.Network:
    """
    Bus + one load named ``L1`` carrying its own ``p_set`` PROFILE.

    Time-varying rather than a scalar on purpose: it puts a column in
    ``loads_t.p_set``, which is the fallback ``GET /timeseries/loads/p_set``
    serves when the user store holds nothing for that column. Without it the
    endpoint answers with an empty frame either way and a leak would be
    indistinguishable from a tenant's own data — the assertions below could then
    only say "not 777", never "100, which is mine".
    """
    n = pypsa.Network()
    idx = SNAPS.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B1", carrier="gas", p_nom=500.0, marginal_cost=10.0)
    n.add("Load", "L1", bus="B1",
          p_set=pd.Series([STATIC_P_SET] * len(idx), index=idx))
    return n


def _upload_load_profile(test_client, value: float = UPLOADED_P_SET):
    """POST a one-column CSV for ``loads/p_set`` the way the GUI's uploader does."""
    csv = pd.DataFrame({"L1": [value] * len(SNAPS)}, index=SNAPS).to_csv()
    resp = test_client.post(
        "/api/network/timeseries/upload",
        params={"component": "loads", "attribute": "p_set"},
        files={"file": ("profile.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp


def _save_as(test_client, name: str):
    resp = test_client.post(
        f"/api/projects/{name}", params={"force": True, "rebind": True}
    )
    assert resp.status_code == 200, resp.text
    return resp


def _p_set_payload(test_client):
    resp = test_client.get("/api/network/timeseries/loads/p_set")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _values_for(payload: dict, column: str) -> list[float]:
    assert column in payload["columns"], (
        f"column {column!r} missing from {payload['columns']!r}"
    )
    col = payload["columns"].index(column)
    return [row[col] for row in payload["data"]]


@pytest.fixture
def org_b_project(other_org_client, install_network):
    """Org B, with its own saved project holding its own static ``L1`` load."""
    install_network(_net_with_one_load())
    _save_as(other_org_client, "B-Baseline")
    return "B-Baseline"


# ── 1. a series uploaded in one project is never readable from another ────────

def test_a_second_org_cannot_read_the_first_orgs_uploaded_column(
    client, other_org_client, install_network, org_b_project,
):
    """
    The finding's own reproduction, as a test.

    Org A uploads a demand profile for ``L1``; org B activates B's OWN project
    and asks for ``loads/p_set``. B must be served B's static 100.0, never A's
    uploaded 777.0 — the endpoint prefers the user store over the network, so a
    shared store means B reads A's demand.
    """
    install_network(_net_with_one_load())
    _upload_load_profile(client)

    assert other_org_client.post(
        f"/api/projects/{org_b_project}/activate"
    ).status_code == 200
    values = _values_for(_p_set_payload(other_org_client), "L1")

    assert UPLOADED_P_SET not in values, (
        "org B was served org A's uploaded demand profile — the user "
        f"time-series store is shared across tenants (got {values!r})"
    )
    assert values == [STATIC_P_SET] * len(SNAPS), values


# ── 2. a save writes only series belonging to the project being saved ─────────

def test_a_save_writes_only_the_saved_projects_own_series(
    client, other_org_client, install_network, org_b_project, project_storage_dir,
    second_identity,
):
    """
    A foreground save serialises the store into ``<project>/user_ts.json`` and
    reapplies it onto the network before the netCDF export. So a foreign column
    in the store at save time does not merely leak a read — it is persisted into
    another tenant's project storage, baked into its ``network.nc``, and fed
    into its solve results.
    """
    install_network(_net_with_one_load())
    _upload_load_profile(client)

    assert other_org_client.post(
        f"/api/projects/{org_b_project}/activate"
    ).status_code == 200
    _save_as(other_org_client, org_b_project)

    user_ts_path = (
        project_storage_dir(org_b_project, second_identity["org_id"]) / "user_ts.json"
    )
    if not user_ts_path.exists():
        return  # nothing persisted at all is trivially not a leak
    saved = json.loads(user_ts_path.read_text())
    leaked = (
        saved.get("loads", {}).get("p_set", {}).get("L1", {}).get("values") or []
    )
    assert UPLOADED_P_SET not in leaked, (
        f"org A's uploaded values were written into {user_ts_path} — a save "
        f"persisted another tenant's series (got {leaked!r})"
    )


# ── 3. a load/activate cannot discard another session's in-flight upload ──────

def test_loading_a_project_does_not_discard_another_sessions_upload(
    client, other_org_client, install_network, org_b_project,
):
    """
    The symmetric half of the same defect. ``load_project`` calls
    ``_restore_user_ts``, which REPLACES the store wholesale — so one tenant
    opening its own project from disk discarded the other's unsaved uploads.

    Deliberately the LOAD path (``GET /api/projects/{name}``) and not
    ``/activate``: activate never touched the store at all, so a test written
    against it would pass on the shared-dict code and prove nothing. The
    property is about both, and this is the half that could break it.
    """
    install_network(_net_with_one_load())
    _upload_load_profile(client)

    assert other_org_client.get(
        f"/api/projects/{org_b_project}"
    ).status_code == 200

    values = _values_for(_p_set_payload(client), "L1")
    assert values == [UPLOADED_P_SET] * len(SNAPS), (
        "org A's in-flight upload was discarded by org B opening its own "
        f"project (got {values!r})"
    )


# ── 4. the store itself is per-context, not per-process ──────────────────────

def test_two_project_contexts_do_not_share_one_user_ts_store():
    """
    The property under the HTTP surface, asserted without it: two
    ``ProjectContext`` objects own two stores. Written as an identity/containment
    check rather than a route assertion so it keeps holding for every FUTURE
    reader of the store, not only the routes that read it today.
    """
    one = ProjectContext(network=pypsa.Network())
    two = ProjectContext(network=pypsa.Network())

    assert one.user_ts is not two.user_ts, (
        "two project contexts resolve to ONE user time-series store"
    )
    one.user_ts[("loads", "p_set", "L1")] = pd.Series(
        [UPLOADED_P_SET] * len(SNAPS), index=SNAPS
    )
    assert ("loads", "p_set", "L1") not in two.user_ts, (
        "a series written for one project is visible from another"
    )


def test_the_module_level_store_resolves_to_the_active_contexts_own_store():
    """
    The wiring that makes the existing ``_user_ts`` call sites tenant-safe
    without touching them: the module-level name is a VIEW of whichever context
    is active for the caller, not a dict of its own. If it ever stops resolving
    per context, every one of those call sites silently shares state again.
    """
    from services.user_timeseries import _user_ts

    one = ProjectContext(network=pypsa.Network())
    two = ProjectContext(network=pypsa.Network())
    key = ("loads", "p_set", "L1")
    series = pd.Series([UPLOADED_P_SET] * len(SNAPS), index=SNAPS)

    token = PyPSAService.bind_request_context(one)
    try:
        _user_ts[key] = series
        assert key in _user_ts
    finally:
        PyPSAService.reset_request_context(token)

    token = PyPSAService.bind_request_context(two)
    try:
        assert key not in _user_ts, (
            "the module-level store served one context's series to another"
        )
    finally:
        PyPSAService.reset_request_context(token)

    assert key in one.user_ts and key not in two.user_ts


# ── 5. the lifecycle the per-context move had to preserve ────────────────────

def test_re_clustering_a_network_keeps_that_projects_uploaded_profiles():
    """
    ``set_network`` (spatial clustering) replaces the network object for the SAME
    project and publishes a NEW ProjectContext to carry it. The store has to come
    across, because nothing repopulates it afterwards — unlike every
    ``reset_network`` caller, which resets in order to load something and then
    sets the store explicitly a few lines later.

    While the store was a module global it survived every swap for free. Per
    context it survives only because `set_network` carries it, so this pins the
    carry: without it a re-cluster silently drops every uploaded profile, and the
    next save writes a ``user_ts.json`` that no longer has them.

    A copy, not the same dict — the outgoing context must not observe writes made
    after the swap, the same rule `solver_state` follows.
    """
    from services.user_timeseries import _user_ts

    before = ProjectContext(network=_net_with_one_load())
    key = ("loads", "p_set", "L1")
    token = PyPSAService.bind_request_context(before)
    try:
        _user_ts[key] = pd.Series([UPLOADED_P_SET] * len(SNAPS), index=SNAPS)

        PyPSAService.set_network(_net_with_one_load())
        after = PyPSAService.get_active_context()
        assert after is not before, "set_network did not publish a new context"

        assert key in after.user_ts, (
            "re-clustering dropped the project's uploaded profiles"
        )
        assert after.user_ts is not before.user_ts, (
            "the store came across by reference, so the discarded context still "
            "observes writes made after the swap"
        )
    finally:
        PyPSAService.reset_request_context(token)


# ── 6. the save reads the store of the context it is saving ──────────────────

def test_a_save_persists_the_saved_contexts_series_not_the_active_ones(
    tmp_projects_dir,
):
    """
    ``_save_context`` serialises ``ctx.user_ts`` — the project it is saving — and
    not whatever store the calling thread happens to resolve.

    Test 2 above shows the property holding over HTTP, where the context being
    saved IS the active one, so it would pass equally if the save read the active
    store by luck. This drives the case the gate used to exist for: a save of a
    NON-active context, with a different context bound on the calling thread and
    a different series in it. Without the explicit pairing, the file under `BG/`
    fills with `FG`'s numbers.
    """
    from routers.projects import _save_context

    key = ("loads", "p_set", "L1")
    series = pd.Series([UPLOADED_P_SET] * len(SNAPS), index=SNAPS)
    other = pd.Series([555.0] * len(SNAPS), index=SNAPS)

    background = ProjectContext(network=_net_with_one_load())
    background.loaded_project = "BG"
    background.user_ts[key] = series

    foreground = ProjectContext(network=_net_with_one_load())
    foreground.loaded_project = "FG"
    foreground.user_ts[key] = other

    token = PyPSAService.bind_request_context(foreground)
    try:
        _save_context(background, "BG", expect="BG")
    finally:
        PyPSAService.reset_request_context(token)

    saved = json.loads((tmp_projects_dir / "BG" / "user_ts.json").read_text())
    values = saved["loads"]["p_set"]["L1"]["values"]
    assert values == [UPLOADED_P_SET] * len(SNAPS), (
        f"the save wrote the ACTIVE context's series into BG's project "
        f"directory instead of BG's own (got {values!r})"
    )
    # …and the active context's own store was not ingested into either.
    assert foreground.user_ts[key].tolist() == other.tolist()
