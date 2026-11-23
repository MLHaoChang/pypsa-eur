"""SCU06 K8b: storage attested to carry no backup obligation earns reserves on
a floor of 0 (SCU06-K8-1 = (a); GK A conditions B1-B5)."""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from services.solver import reserve_products as RP  # noqa: E402
from tests.test_reserve_products import (  # noqa: E402
    TOL,
    UPS_FLOOR,
    _bind,
    _cfg,
    _dc_project,
    _product,
    _strings,
    _ups_site,
)


BESS = "bess"


def _bess_site(snaps=4, p_nom=10.0, max_hours=1.0, load=None, gen_avail=None):
    """One BESS with no backup role on a one-bus site with a free-ish supply."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="site", p_nom=100.0, marginal_cost=1.0)
    if gen_avail is not None:
        n.generators_t.p_max_pu = pd.DataFrame(
            {"supply": gen_avail}, index=n.snapshots
        )
    if load is not None:
        n.add("Load", "site_load", bus="site", p_set=pd.Series(load, index=n.snapshots))
    n.add(
        "StorageUnit",
        BESS,
        bus="site",
        p_nom=p_nom,
        max_hours=max_hours,
        cyclic_state_of_charge=True,
    )
    return n


def _solve_attested(n, products, prices=None):
    prices = prices or {p.id: 10.0 for p in products}
    _bind(n, products, prices)
    floors, strings = RP.derive_backup(n, n._k8b_dir, [BESS])
    fn = RP._wrap_with_reserve_products(
        n, None, _cfg(products), backup=(floors, strings)
    )
    status, _ = n.optimize(
        solver_name="highs", extra_functionality=fn, include_objective_constant=False
    )
    assert status == "ok"
    assert RP.persist_solution(n)
    frame, flags = RP.solved(n)
    assert flags == []
    return frame


def _plain_project(tmp_path, n):
    """A saved project with no DC spec and no island sidecar (an IPP BESS)."""
    n._k8b_dir = tmp_path
    return n


def test_b1_an_attested_storage_is_the_only_floor_0_source(tmp_path):
    n = _plain_project(tmp_path, _bess_site())
    assert RP.derive_backup(n, tmp_path, [BESS]) == ({BESS: 0.0}, frozenset())
    # No attestation: nothing eligible, and the product naming it is refused.
    assert RP.derive_backup(n, tmp_path, []) == ({}, frozenset())
    assert RP.derive_backup(n, tmp_path) == ({}, frozenset())
    p = _product(storages=[BESS])
    _bind(n, [p], {"fcr": 10.0})
    with pytest.raises(RP.ReserveRefusal) as e:
        RP.plan(n, [p], *RP.derive_backup(n, tmp_path, []))
    assert e.value.kind == RP.NOT_ELIGIBLE
    assert "attested as having no backup obligation" in str(e.value)
    # An attestation names a storage the network does not hold: nothing.
    assert RP.derive_backup(n, tmp_path, ["ghost"]) == ({}, frozenset())
    # An unsaved project: the roles cannot be read, so no attestation counts.
    assert RP.derive_backup(n, None, [BESS]) == ({}, frozenset())
    assert RP.attestation_refusal(n, None, [BESS]) is not None
    assert RP.attestation_refusal(n, None, []) is None


def test_b1_unreadable_roles_keep_an_attested_storage_not_eligible(tmp_path):
    import json

    from services.adequacy import dc_backup

    n = _plain_project(tmp_path, _bess_site())
    (tmp_path / "island_config.json").write_text("{not json")
    assert RP.derive_backup(n, tmp_path, [BESS]) == ({}, frozenset())
    (tmp_path / "island_config.json").write_text(json.dumps(["a list"]))
    assert RP.derive_backup(n, tmp_path, [BESS]) == ({}, frozenset())
    # A data-centre project whose spec does not read: its strings are unknown.
    (tmp_path / "island_config.json").unlink()
    (tmp_path / dc_backup.META_FILE).write_text(
        json.dumps({"id": "eh_datacenter", "dc_load": {"spec": {"bad": 1}}})
    )
    assert RP.derive_backup(n, tmp_path, [BESS]) == ({}, frozenset())
    assert RP.attestation_refusal(n, tmp_path, [BESS]) is not None


def test_b1_an_unreadable_ride_through_floor_is_never_0_by_attestation(tmp_path):
    n = _ups_site(tmp_path, UPS_FLOOR + 1.0)
    n.storage_units.loc["ups", "efficiency_dispatch"] = 0.0
    assert RP.derive_backup(n, tmp_path, ["ups"])[0] == {}


def test_b2_backup_roles_refuse_the_attestation(tmp_path):
    from services.adequacy import dc_backup

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    roles = RP.backup_roles(n, tmp_path)
    strings = _strings(n)
    assert strings and set(strings) <= roles
    for s in strings:
        why = RP.attestation_refusal(n, tmp_path, [s])
        assert why is not None and s in why
        floors, _ = RP.derive_backup(n, tmp_path, [s])
        assert floors[s] > 0  # the DC-03 floor stays
    # A spare is a role whether or not the network holds it (string_names).
    spare = f"{dc_backup.SPARE_PREFIX}1"
    n.add("StorageUnit", spare, bus=n.storage_units.at[strings[0], "bus"], p_nom=1.0,
          max_hours=1.0)
    if spare in roles:
        assert RP.attestation_refusal(n, tmp_path, [spare]) is not None
        assert spare not in RP.derive_backup(n, tmp_path, [spare])[0] or \
            RP.derive_backup(n, tmp_path, [spare])[0][spare] > 0


def test_b2_a_spare_is_refused(tmp_path):
    from services.adequacy import dc_backup

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    sizing = {"strings": 3, "strings_n": 2}
    names = dc_backup.string_names(sizing)
    assert names[-1].startswith(dc_backup.SPARE_PREFIX)
    real = RP._derive_dc
    try:
        RP._derive_dc = lambda n_, d_: ({}, frozenset(), frozenset(names))
        assert RP.attestation_refusal(n, tmp_path, [names[-1]]) is not None
        assert RP.derive_backup(n, tmp_path, [names[-1]]) == ({}, frozenset())
    finally:
        RP._derive_dc = real


def test_b2_ride_through_storage_is_refused(tmp_path):
    n = _ups_site(tmp_path, UPS_FLOOR + 1.0)
    why = RP.attestation_refusal(n, tmp_path, ["ups"])
    assert why is not None and "ups" in why
    assert RP.derive_backup(n, tmp_path, ["ups"])[0] == {
        "ups": pytest.approx(UPS_FLOOR, rel=1e-12)
    }
    # The same on a project with no DC spec whose island sidecar lists it.
    import json

    from services.adequacy import dc_backup

    (tmp_path / dc_backup.META_FILE).unlink()
    (tmp_path / "island_config.json").write_text(
        json.dumps({"requirements": {"ride_through_storage": ["ups"]}})
    )
    assert RP.attestation_refusal(n, tmp_path, ["ups"]) is not None
    assert RP.derive_backup(n, tmp_path, ["ups"]) == ({}, frozenset())


def test_b2_an_attestation_beside_a_dc_project_counts(tmp_path):
    n = _dc_project(tmp_path, {"mode": "hybrid"})
    bus = n.storage_units.bus.iloc[0]
    n.add("StorageUnit", BESS, bus=bus, p_nom=1.0, max_hours=1.0)
    assert RP.attestation_refusal(n, tmp_path, [BESS]) is None
    floors, strings = RP.derive_backup(n, tmp_path, [BESS])
    plain, plain_strings = RP.derive_backup(n, tmp_path)
    assert floors == {**plain, BESS: 0.0}
    assert strings == plain_strings


def test_b3_no_attestation_equals_master_and_one_moves_the_inputs_digest(tmp_path):
    from models.commercial import CommercialConfig
    from services.commercial import hashing

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    bus = n.storage_units.bus.iloc[0]
    n.add("StorageUnit", BESS, bus=bus, p_nom=1.0, max_hours=1.0)
    plain = RP.derive_backup(n, tmp_path)
    assert RP.derive_backup(n, tmp_path, []) == plain
    every = _product()  # storages empty: every eligible storage
    assert RP.inputs_digest([every], RP.derive_backup(n, tmp_path, [])[0]) == \
        RP.inputs_digest([every], plain[0])
    widened = RP.derive_backup(n, tmp_path, [BESS])[0]
    assert RP.inputs_digest([every], widened) != RP.inputs_digest([every], plain[0])
    # A config without the field dumps and hashes as master's.
    a = CommercialConfig(poc_link="poc")
    b = CommercialConfig(poc_link="poc", reserve_no_backup_storages=[])
    assert "reserve_no_backup_storages" not in a.model_dump(mode="json")
    assert a.model_dump_json() == b.model_dump_json()
    assert hashing.digest(a) == hashing.digest(b)
    c = CommercialConfig(poc_link="poc", reserve_no_backup_storages=[BESS])
    assert hashing.digest(c) != hashing.digest(a)
    with pytest.raises(ValueError):
        CommercialConfig(poc_link="poc", reserve_no_backup_storages=[BESS, BESS])


def test_b3_the_fingerprint_equals_master_without_and_moves_with(tmp_path):
    from services.solver_service import SolverConfig
    from services.workflow_tools import _network_input_fingerprint

    n = _dc_project(tmp_path, {"mode": "hybrid"})
    bus = n.storage_units.bus.iloc[0]
    n.add("StorageUnit", BESS, bus=bus, p_nom=1.0, max_hours=1.0)
    from models.commercial import CommercialConfig

    def fp(**kw):
        # Stored as the PUT stores it: the typed config's dump.
        commercial = CommercialConfig(
            poc_link="poc", reserve_products=[_product()], **kw
        ).model_dump(mode="json")
        return _network_input_fingerprint(
            SimpleNamespace(
                network=n,
                solver_state={"solver_config": SolverConfig(commercial=commercial)},
                user_ts={},
                storage_dir=str(tmp_path),
            )
        )

    master = fp()
    assert fp(reserve_no_backup_storages=[]) == master
    assert fp(reserve_no_backup_storages=[BESS]) != master


def test_b4_an_up_product_where_the_energy_rows_bind_first(tmp_path):
    """p_nom 10, max_hours 1 (10 MWh), floor 0 (attested), sustain 2 h: the up
    energy row soc - 2 r >= 0 with soc <= 10 gives r <= 5 below the power
    bound 10. Revenue 4 h x 10 x 5 = 200 is the objective's only term."""
    n = _plain_project(tmp_path, _bess_site())
    up = _product("up", kind="secondary", direction="up", sustain_h=2.0)
    frame = _solve_attested(n, [up])
    assert frame[f"{BESS}|up"].to_numpy() == pytest.approx([5.0] * 4, abs=TOL)
    assert n.objective == pytest.approx(-200.0, abs=1e-4)


def test_b4_a_symmetric_product_where_the_energy_rows_bind_first(tmp_path):
    """Symmetric, sustain 2 h on 10 MWh: soc >= 2 r and soc + 2 r <= 10, so
    r <= 2.5 at soc 5. Revenue 4 x 10 x 2.5 = 100."""
    n = _plain_project(tmp_path, _bess_site())
    sym = _product(sustain_h=2.0)
    frame = _solve_attested(n, [sym])
    assert frame[f"{BESS}|fcr"].to_numpy() == pytest.approx([2.5] * 4, abs=TOL)
    assert n.objective == pytest.approx(-100.0, abs=1e-4)


def test_b4_energy_and_reserve_never_share_a_mw(tmp_path):
    """The supply is out in the last hour, so the BESS serves the 6 MW load
    there: the up power row p_dis - p_store + r <= 10 leaves 10 - 6 = 4 MW for
    the reserve in that hour. The 6 MWh is charged (at 1) in the first three
    hours, where stopping the charge is up headroom: their r sums to 30 + 6.
    Energy rows are slack (sustain 0.1 h on 40 MWh).
    Objective 6 - 10 x (36 + 4) = -394."""
    n = _plain_project(
        tmp_path,
        _bess_site(max_hours=4.0, load=[0.0, 0.0, 0.0, 6.0], gen_avail=[1, 1, 1, 0]),
    )
    up = _product("up", kind="secondary", direction="up", sustain_h=0.1)
    frame = _solve_attested(n, [up])
    r = frame[f"{BESS}|up"].to_numpy()
    assert r[3] == pytest.approx(4.0, abs=TOL)
    assert r[:3].sum() == pytest.approx(36.0, abs=1e-5)
    assert n.storage_units_t.p_dispatch[BESS].iloc[3] == pytest.approx(6.0, abs=TOL)
    assert n.objective == pytest.approx(-394.0, abs=1e-4)


def test_project_backup_reads_the_attestation_of_the_config_being_solved(
    tmp_path, monkeypatch
):
    from services.pypsa_service import PyPSAService

    n = _plain_project(tmp_path, _bess_site())
    stored = {"solver_config": SimpleNamespace(
        commercial={"reserve_no_backup_storages": [BESS]})}
    ctx = SimpleNamespace(storage_dir=str(tmp_path), solver_state=stored)
    monkeypatch.setattr(PyPSAService, "solve_target_context", lambda: ctx)
    # Default: the project's current config.
    assert RP.project_backup(n) == ({BESS: 0.0}, frozenset())
    # A config passed in (the one being solved) wins over the stored one.
    assert RP.project_backup(n, _cfg([])) == ({}, frozenset())
    assert RP.project_backup(n, SimpleNamespace(
        commercial={"reserve_no_backup_storages": [BESS]})) == ({BESS: 0.0}, frozenset())
    # Unsaved: nothing, whatever the config attests.
    monkeypatch.setattr(PyPSAService, "solve_target_context",
                        lambda: SimpleNamespace(storage_dir=None, solver_state=stored))
    assert RP.project_backup(n) == ({}, frozenset())
    monkeypatch.setattr(PyPSAService, "solve_target_context", lambda: None)
    assert RP.project_backup(n) == ({}, frozenset())
