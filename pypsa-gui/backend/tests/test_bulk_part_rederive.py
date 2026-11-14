"""
TCU 08: a bulk edit of a composite cost part re-derives the asset's cost columns, exactly as the single edit does.

A single edit (PUT) runs `derive.apply_parts`, so `capital_cost`, `fom_cost` and `lifetime` (hashed, LP-read
columns) follow the parts. The bulk route wrote the raw part columns only: the solve staging re-derived
`capital_cost` behind it, so the LP moved while the input fingerprint did not (old results read as current), and
`fom_cost` / `lifetime` never moved at all. GK B rulings: "a bulk write applies its side effects per target"
(#1466 D1), "a bulk path runs the same validators as the single edit" (#738).

Each case edits the same value through PUT on one network and through the bulk route on its twin, and compares
the derived columns; the bulk side also checks that the cost and the fingerprint moved.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from tests.conftest import build_network

BULK = "/api/network/_bulk"

BATTERY = {
    "inv_power_overnight": 300_000.0,
    "inv_power_lifetime": 10.0,
    "inv_power_fom_share": 0.01,
    "inv_energy_overnight": 150_000.0,
    "inv_energy_lifetime": 20.0,
    "inv_energy_fom_share": 0.02,
}
LINE = {"inv_per_km_overnight": 432.0, "inv_per_km_fom_share": 0.02}
DERIVED = ("capital_cost", "fom_cost", "lifetime")


def _network():
    from services.asset_schema.derive import apply_parts

    n = build_network()
    n.add("Bus", "B2")
    for name in ("bess", "bess2"):
        n.add(
            "StorageUnit",
            name,
            bus="B1",
            p_nom=10.0,
            max_hours=2.0,
            p_nom_extendable=True,
        )
        apply_parts(n, "StorageUnit", name, BATTERY, discount_rate=0.07)
    n.add(
        "Line",
        "ab",
        bus0="B1",
        bus1="B2",
        s_nom_extendable=True,
        length=100.0,
        x=0.1,
        lifetime=40.0,
    )
    apply_parts(n, "Line", "ab", LINE, discount_rate=0.07)
    return n


def _fingerprint():
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    return _network_input_fingerprint(PyPSAService.get_active_context())


def _derived(n, cls_attr, name):
    df = getattr(n, cls_attr)
    return {c: float(df.at[name, c]) for c in DERIVED}


CASES = [
    # (class, attr, route, name, column, new value, the derived column the edit must move)
    (
        "StorageUnit",
        "storage_units",
        "storage_units",
        "bess",
        "inv_power_overnight",
        500_000.0,
        "capital_cost",
    ),
    (
        "StorageUnit",
        "storage_units",
        "storage_units",
        "bess",
        "inv_power_lifetime",
        25.0,
        "lifetime",
    ),
    (
        "StorageUnit",
        "storage_units",
        "storage_units",
        "bess",
        "inv_energy_fom_share",
        0.05,
        "fom_cost",
    ),
    ("Line", "lines", "lines", "ab", "inv_per_km_overnight", 600.0, "capital_cost"),
    ("Line", "lines", "lines", "ab", "inv_per_km_fom_share", 0.04, "fom_cost"),
]


@pytest.mark.parametrize("row_form", [False, True], ids=["names", "rows"])
@pytest.mark.parametrize(
    "cls,attr,route,name,col,value,moves", CASES, ids=[c[4] for c in CASES]
)
def test_bulk_part_edit_rederives_like_put(
    client, install_network, cls, attr, route, name, col, value, moves, row_form
):
    put_net = install_network(_network())
    buses = {"bus": "B1"} if cls == "StorageUnit" else {"bus0": "B1", "bus1": "B2"}
    r = client.put(
        f"/api/network/{route}/{name}", json={"name": name, **buses, col: value}
    )
    assert r.status_code == 200, r.text
    expected = _derived(put_net, attr, name)

    n = install_network(_network())
    before = _derived(n, attr, name)
    fp_before = _fingerprint()
    body = (
        {"component_class": cls, "rows": [{"name": name, "updates": {col: value}}]}
        if row_form
        else {"component_class": cls, "names": [name], "updates": {col: value}}
    )
    r = client.patch(BULK, json=body)
    assert r.status_code == 200, r.text

    after = _derived(n, attr, name)
    assert after[moves] != pytest.approx(before[moves])
    assert after == expected  # T5: the same apply_parts call, so bit-equal
    assert _fingerprint() != fp_before


def test_bulk_rederives_every_target_on_its_own_row(client, install_network):
    """Per target (#1466 D1): two batteries with different energy scales each get their own cost."""
    n = install_network(_network())
    n.storage_units.at["bess2", "max_hours"] = 4.0
    r = client.patch(
        BULK,
        json={
            "component_class": "StorageUnit",
            "names": ["bess", "bess2"],
            "updates": {"inv_energy_overnight": 200_000.0},
        },
    )
    assert r.status_code == 200, r.text
    one, two = (float(n.storage_units.at[t, "capital_cost"]) for t in ("bess", "bess2"))
    from services.asset_schema.derive import derive_composite

    for t, got in (("bess", one), ("bess2", two)):
        row = n.storage_units.loc[t]
        want = derive_composite(
            "StorageUnit",
            row,
            scales={"max_hours": float(row["max_hours"])},
            discount_rate=0.07,
            asset_lifetime=row["lifetime"],
        )["capital_cost"]
        assert got == pytest.approx(want, rel=1e-12)
    assert two > one


def test_bulk_scale_edit_rederives(client, install_network):
    """A bulk `max_hours` or `length` edit rescales the part, as the single edit does."""
    n = install_network(_network())
    su, line = (
        float(n.storage_units.at["bess", "capital_cost"]),
        float(n.lines.at["ab", "capital_cost"]),
    )
    assert (
        client.patch(
            BULK,
            json={
                "component_class": "StorageUnit",
                "names": ["bess"],
                "updates": {"max_hours": 4.0},
            },
        ).status_code
        == 200
    )
    assert (
        client.patch(
            BULK,
            json={
                "component_class": "Line",
                "names": ["ab"],
                "updates": {"length": 200.0},
            },
        ).status_code
        == 200
    )
    assert float(n.storage_units.at["bess", "capital_cost"]) > su
    assert float(n.lines.at["ab", "capital_cost"]) == pytest.approx(2 * line, rel=1e-12)


@pytest.mark.parametrize("row_form", [False, True], ids=["names", "rows"])
def test_bulk_refuses_a_priced_part_without_a_lifetime_and_writes_nothing(
    client, install_network, row_form
):
    """The single edit's validator, every target before the first write (T1): only the SECOND target is invalid,
    and the 422 leaves the whole frame as it was, the valid first target included."""
    n = install_network(_network())
    n.storage_units.at["bess2", "inv_power_lifetime"] = float(
        "nan"
    )  # bess2's power part has no lifetime of its own
    n.storage_units.at["bess2", "inv_power_overnight"] = float(
        "nan"
    )  # ... and is unpriced, so it is valid as stored
    before = n.storage_units.copy()
    body = (
        {
            "component_class": "StorageUnit",
            "rows": [
                {"name": "bess", "updates": {"inv_power_overnight": 500_000.0}},
                {"name": "bess2", "updates": {"inv_power_overnight": 500_000.0}},
            ],
        }
        if row_form
        else {
            "component_class": "StorageUnit",
            "names": ["bess", "bess2"],
            "updates": {"inv_power_overnight": 500_000.0},
        }
    )
    r = client.patch(BULK, json=body)
    assert r.status_code == 422, r.text
    assert "lifetime" in r.json()["detail"]
    pd.testing.assert_frame_equal(n.storage_units, before)


def test_bulk_of_an_unrelated_column_leaves_the_derived_columns_byte_equal(
    client, install_network
):
    n = install_network(_network())
    before = {t: _derived(n, "storage_units", t) for t in ("bess", "bess2")}
    r = client.patch(
        BULK,
        json={
            "component_class": "StorageUnit",
            "names": ["bess", "bess2"],
            "updates": {"p_nom": 20.0},
        },
    )
    assert r.status_code == 200, r.text
    assert {t: _derived(n, "storage_units", t) for t in ("bess", "bess2")} == before


# ── T6: the callers through bulk ────────────────────────────────────────────


def test_the_assistant_apply_moves_cost_and_fingerprint(install_network):
    from services import chat_tools

    n = install_network(_network())
    cost, fp = float(n.storage_units.at["bess", "capital_cost"]), _fingerprint()
    chat_tools.bulk_update_components(
        "StorageUnit", ["bess"], {"inv_power_overnight": 500_000.0}
    )
    assert float(n.storage_units.at["bess", "capital_cost"]) > cost
    assert _fingerprint() != fp


def test_the_sweep_child_path_moves_cost_and_fingerprint(install_network):
    """The sensitivity sweep's case build: a private context over a copy of the baseline, edited through the chat
    tool (`workflow_tools.run_sensitivity_sweep`). `max_hours` is on the sweep's allowlist and scales the part."""
    import copy

    from services import chat_tools
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _isolated, _network_input_fingerprint

    install_network(_network())
    base = PyPSAService.get_active_context()
    base_fp = _network_input_fingerprint(base)
    ctx = PyPSAService.build_context()
    ctx.network = base.network.copy()
    ctx.solver_state["solver_config"] = copy.deepcopy(
        base.solver_state["solver_config"]
    )
    with _isolated(ctx):
        chat_tools.bulk_update_components(
            "StorageUnit", ["bess"], {"inv_energy_overnight": 200_000.0}
        )
    case = ctx.network.storage_units
    assert float(case.at["bess", "capital_cost"]) > float(
        base.network.storage_units.at["bess", "capital_cost"]
    )
    assert _network_input_fingerprint(ctx) != base_fp


# ── T4: the solve staging re-derives every derived column ───────────────────


def _stage(n):
    from services.solver.periodized_costs import fill_periodized_cost_defaults
    from services.solver_service import SolverConfig

    return fill_periodized_cost_defaults(n, SolverConfig(discount_rate=0.07))


def test_staging_rederives_fom_cost_and_lifetime_a_raw_writer_left_stale():
    stale, fresh = _network(), _network()
    from services.asset_schema.derive import apply_parts

    stale.storage_units.at["bess", "inv_power_fom_share"] = (
        0.05  # a raw write: derived columns not moved
    )
    stale.storage_units.at["bess", "inv_power_lifetime"] = 25.0
    apply_parts(
        fresh,
        "StorageUnit",
        "bess",
        {"inv_power_fom_share": 0.05, "inv_power_lifetime": 25.0},
        discount_rate=0.07,
    )
    stored = stale.storage_units[list(DERIVED)].copy()
    revert = _stage(stale)
    revert_fresh = _stage(fresh)
    try:
        pd.testing.assert_frame_equal(
            stale.storage_units[list(DERIVED)], fresh.storage_units[list(DERIVED)]
        )
    finally:
        revert()
        revert_fresh()
    pd.testing.assert_frame_equal(
        stale.storage_units[list(DERIVED)], stored
    )  # the revert restores what was stored


def test_staging_a_row_already_in_agreement_is_bit_identical():
    """The pin: a consistent row's LP inputs are exactly what the capital_cost-only staging gave."""
    from services.solver.periodized_costs import fom_horizon_factor

    n = _network()
    stored = {
        a: getattr(n, a)[list(DERIVED)].copy() for a in ("storage_units", "lines")
    }
    factor = fom_horizon_factor(n)
    revert = _stage(n)
    try:
        for a, frame in stored.items():
            staged = getattr(n, a)
            pd.testing.assert_series_equal(
                staged["lifetime"], frame["lifetime"], check_exact=True
            )
            pd.testing.assert_series_equal(
                staged["fom_cost"], frame["fom_cost"] * factor, check_exact=True
            )
            pd.testing.assert_series_equal(
                staged["capital_cost"], frame["capital_cost"] * factor, check_exact=True
            )
    finally:
        revert()


def test_bulk_on_an_unpriced_row_leaves_its_typed_cost(client, install_network):
    """A battery typed the old way (a bare capital_cost, no parts) keeps what the bulk wrote."""
    n = build_network()
    n.add(
        "StorageUnit", "plain", bus="B1", p_nom=10.0, max_hours=2.0, capital_cost=1234.0
    )
    n = install_network(n)
    r = client.patch(
        BULK,
        json={
            "component_class": "StorageUnit",
            "names": ["plain"],
            "updates": {"capital_cost": 999.0},
        },
    )
    assert r.status_code == 200, r.text
    assert float(n.storage_units.at["plain", "capital_cost"]) == 999.0
    assert not math.isnan(float(n.storage_units.at["plain", "capital_cost"]))


# ── T3: the fingerprint backstop for a raw writer of a part ─────────────────


def _ctx(n):
    from types import SimpleNamespace

    from services.solver_service import SolverConfig

    return SimpleNamespace(
        network=n,
        solver_state={"solver_config": SolverConfig()},
        user_ts={},
        storage_dir=None,
    )


def test_a_network_without_a_priced_part_fingerprints_byte_identical_to_before():
    """Values from master e73996c71 (AS-02 pin): no part column, or one that holds no value, appends nothing."""
    from services.workflow_tools import _network_input_fingerprint as fp

    n = build_network()
    n.add("Bus", "B2")
    n.add(
        "StorageUnit", "bess", bus="B1", p_nom=10.0, max_hours=2.0, capital_cost=1234.0
    )
    n.add(
        "Line",
        "ab",
        bus0="B1",
        bus1="B2",
        length=100.0,
        x=0.1,
        s_nom=50.0,
        capital_cost=5.0,
    )
    for _ in range(2):
        assert (
            fp(_ctx(n))
            == "35fd1ebb6193b1f925890baec8fec756bd2da965a2fa30f8878f8def2082eae6"
        )
        assert (
            fp(_ctx(n), exact=True)
            == "1a30168d2df520d9ddd9965cd27115e4ff361199a49d36e2115ca886a24be633"
        )
        n.storage_units["inv_power_overnight"] = float(
            "nan"
        )  # an unpriced column, as a PUT leaves it


@pytest.mark.parametrize(
    "attr,name,col,value",
    [
        ("storage_units", "bess", "inv_power_fom_share", 0.05),
        ("lines", "ab", "inv_per_km_overnight", 600.0),
    ],
)
@pytest.mark.parametrize("exact", [False, True])
def test_a_raw_part_write_moves_the_fingerprint(attr, name, col, value, exact):
    """A writer that sets a part and leaves the derived columns as they were still moves the fingerprint."""
    from services.workflow_tools import _network_input_fingerprint as fp

    n = _network()
    before = fp(_ctx(n), exact=exact)
    getattr(n, attr).at[name, col] = value
    assert fp(_ctx(n), exact=exact) != before
