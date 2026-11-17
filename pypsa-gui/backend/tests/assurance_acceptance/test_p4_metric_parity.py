"""AA-10 S4 (A4.6): one metric, one number, on every surface that shows it.

For bus-price/1, carrier-energy/1, curtailment/1, network-emissions/1 and
lost-load/1, on one hand-worked three-snapshot network with non-uniform
snapshot weightings (1, 0.5, 2: one fractional), the checked answer's value,
unit, window and unavailable reasons equal the matching `/results/*` route
value and the results-workbook cell, and both equal hand arithmetic.

- Every expected value is a literal worked by hand in the comment beside it
  (S1), never computed through a service or a shared helper.
- Surfaces are compared exactly. The LP here is solved outright by presolve,
  so its dispatch and duals are exact small numbers and every hand value is a
  float the arithmetic reaches without rounding.
- Metamorphic fixtures: doubled snapshot weightings, reordered and renamed
  components, and a window split, each against the whole-run checked answer.
- An injected wrong aggregate on a surface's service makes the parity check
  fail. None of these surfaces has a runtime cross-check, so no refusal is
  claimed; the parity assertion is the guard.

Known disagreements (S5), each left to a `_pending` row in issue 10's manifest:
the workbook's "Mean price" is the plain mean over snapshots, the checked
mean_price is generator-weighted (ticket 22); the Emissions route and sheet
divide by the static efficiency, the checked answer by the time-varying one
(ticket 23). Neither wrong value is asserted.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import io
import math

import pandas as pd
import pypsa
import pytest
from openpyxl import load_workbook

from harness import calculations
from services import assurance_records as records
from tests.test_lost_load_assurance import solve as solve_through_api
from tests.test_result_profiles_assurance import solve

STAMPS = ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-01T02:00:00"]
WEIGHTS = [1.0, 0.5, 2.0]
PLAIN = {"B1": "B1", "G1": "G1", "G2": "G2", "W1": "W1", "L1": "L1"}
# Renamed so that alphabetical, insertion and original orders all differ.
RENAMED = {"B1": "north", "G1": "g_b", "G2": "g_a", "W1": "w_z", "L1": "demand"}
PROFILES = [
    "bus-price/1",
    "carrier-energy/1",
    "curtailment/1",
    "network-emissions/1",
    "lost-load/1",
]
ENTITY = {
    "bus-price/1": "B1",
    "carrier-energy/1": "gas",
    "curtailment/1": "network",
    "network-emissions/1": "network",
    "lost-load/1": "L1",
}
VIEW = {
    "bus-price/1": ("prices", "Prices"),
    "carrier-energy/1": ("dispatch", "Dispatch"),
    "curtailment/1": ("curtailment", "Curtailment"),
    "network-emissions/1": ("emissions", "Emissions"),
    "lost-load/1": ("lostload", "Lost load"),
}

# Priced network: B1; G1 gas 20 MW at 10 EUR/MWh, efficiency 0.5; G2 gas 50 MW
# at 30, efficiency 0.4; W1 wind 10 MW at 0, p_max_pu 1, 0.5, 0.8; L1 5, 15, 40.
# Dispatch: t0 W1 5 (of 10), price 0; t1 W1 5, G1 10, price 10; t2 W1 8, G1 20,
# G2 12, price 30. Weights 1, 0.5, 2 (sum 3.5). VoLL 30 EUR/MWh.
# Short network (lost-load/1, solved through the API with VoLL 1000): G1 12 MW
# at 10 and G2 8 MW at 20 against L1 5, 25, 30: shed 0, 5, 10 MW.
HAND = {
    "bus-price/1": {
        "mean_price": 18.571428571428573,  # (1*0 + 0.5*10 + 2*30) / 3.5 = 65 / 3.5
        "min_price": 0.0,
        "max_price": 30.0,
        "hours_at_voll": 2.0,  # only t2: 30 >= 30*(1 - 1e-6), weight 2
    },
    "carrier-energy/1": {
        "carrier_generator_energy": 69.0,  # 1*0 + 0.5*10 + 2*(20 + 12) = 5 + 64
        "carrier_peak_generator_output": 32.0,  # t2: 20 + 12
        "carrier_storage_discharged_energy": 0.0,  # no gas StorageUnit: exactly zero
        "carrier_storage_charged_energy": 0.0,
    },
    "curtailment/1": {
        "available_energy": 28.5,  # 1*10 + 0.5*5 + 2*8
        "curtailed_energy": 5.0,  # 1*(10 - 5) + 0.5*(5 - 5) + 2*(8 - 8)
        "curtailment_share": 17.54385964912281,  # 100 * 5 / 28.5
    },
    "network-emissions/1": {
        "total_emissions": 37.5,  # G1 (0.5*10 + 2*20)/0.5*0.25 = 22.5; G2 2*12/0.4*0.25 = 15
    },
    "lost-load/1": {
        "lost_load_energy": 22.5,  # 1*0 + 0.5*5 + 2*10
        "peak_shed": 10.0,
        "shed_hours": 2.5,  # 0.5 + 2: shed above 1e-3 MW at t1 and t2
        "lost_load_cost": 22500.0,  # 1000 * (1*0 + 0.5*5 + 2*10), objective weights
    },
}
REASONS = {p: {} for p in PROFILES} | {
    "network-emissions/1": {"co2_cap_shadow_price": "no_co2_cap"}
}
# Energy, cost and hours: additive over disjoint windows and doubled with the weights.
ADDITIVE = {
    "bus-price/1": ["hours_at_voll"],
    "carrier-energy/1": [
        "carrier_generator_energy",
        "carrier_storage_discharged_energy",
        "carrier_storage_charged_energy",
    ],
    "curtailment/1": ["available_energy", "curtailed_energy"],
    "network-emissions/1": ["total_emissions"],
    "lost-load/1": ["lost_load_energy", "shed_hours", "lost_load_cost"],
}
MAXIMA = {
    "bus-price/1": ["max_price"],
    "carrier-energy/1": ["carrier_peak_generator_output"],
    "lost-load/1": ["peak_shed"],
}
MINIMA = {"bus-price/1": ["min_price"]}


@pytest.fixture(autouse=True)
def parity_store(tmp_path):
    """A private app-data directory, every profile on. Its own MonkeyPatch
    context, not the test's (review-gk.md, #1132)."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
        patch.setenv(
            "PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(calculations.PROFILES)
        )
        yield


def _network(names, scale, short):
    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(STAMPS))
    for column in ("objective", "stores", "generators"):
        n.snapshot_weightings[column] = [scale * w for w in WEIGHTS]
    n.add("Carrier", ["gas", "wind"], co2_emissions=[0.25, 0.0])
    n.add("Bus", names["B1"])
    if short:
        units = [
            ("G1", {"p_nom": 12, "marginal_cost": 10}),
            ("G2", {"p_nom": 8, "marginal_cost": 20}),
        ]
        load = [5, 25, 30]
    else:
        units = [
            (
                "G1",
                {"carrier": "gas", "p_nom": 20, "marginal_cost": 10, "efficiency": 0.5},
            ),
            (
                "G2",
                {"carrier": "gas", "p_nom": 50, "marginal_cost": 30, "efficiency": 0.4},
            ),
            (
                "W1",
                {
                    "carrier": "wind",
                    "p_nom": 10,
                    "marginal_cost": 0,
                    "p_max_pu": [1.0, 0.5, 0.8],
                },
            ),
        ]
        load = [5, 15, 40]
    if names is RENAMED:
        units.reverse()
    for key, attrs in units:
        n.add("Generator", names[key], bus=names["B1"], **attrs)
    n.add("Load", names["L1"], bus=names["B1"], p_set=load)
    return n


@pytest.fixture
def solved(baseline, client, session_state):
    """`solved(profile, names=PLAIN, scale=1, tweak=None)`: the session's
    network replaced by the hand network (`tweak(n)` applied) and solved;
    returns (ctx, entity)."""
    ctx = baseline[0]

    def make(profile, names=PLAIN, scale=1.0, tweak=None):
        short = profile == "lost-load/1"
        ctx.network = _network(names, scale, short)
        if tweak:
            tweak(ctx.network)
        if short:
            # lost-load/1's VoLL slacks exist only in the API solve.
            solve_through_api(client, session_state, voll=1000.0)
        else:
            solve(ctx, voll=30)
        entity = ENTITY[profile]
        return ctx, names.get(entity, entity)

    return make


def _checked(ctx, profile, entity, window=None):
    scope = {
        "version": 1,
        "profile": profile,
        "project_id": ctx.project_uuid,
        "entity_id": entity,
        "retain": False,
    }
    if window:
        scope.update(start=window[0], end=window[1])
    record = records.capture_scoped(records.actor_id(), scope)
    values = {f["metric"]: f["value"] for f in record["facts"]}
    reasons = {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }
    return record, values, reasons


def _get(client, path):
    response = client.get("/api/results/" + path)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def _workbook(client, profile):
    view, title = VIEW[profile]
    response = client.get("/api/results/workbook.xlsx", params={"views": view})
    assert response.status_code == 200, response.text
    return load_workbook(io.BytesIO(response.content))[title]


def _key(ws, label):
    """(value, unit) of a key result's whole-horizon cell."""
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row[0] == label and row[2] == "horizon":
            return row[3], row[1]
    raise AssertionError(f"no key {label!r} on {ws.title}")


def _column(ws, title, column):
    """The values of one column of a sheet table titled `title (unit)`."""
    rows = list(ws.iter_rows(values_only=True))
    start = next(
        i
        for i, r in enumerate(rows)
        if isinstance(r[0], str) and r[0].startswith(title + " (")
    )
    header = list(rows[start + 1])
    out = []
    for row in rows[start + 2 :]:
        if row[0] is None:
            break
        out.append(row[header.index(column)])
    return out


def _surfaces(client, profile, names):
    """Per metric: (route value, workbook (value, unit) or None); the route's
    snapshot index (None for a whole-horizon route), and route reasons."""
    ws = _workbook(client, profile)
    if profile == "bus-price/1":
        prices = _get(client, "prices")
        bus = names["B1"]
        series = [row[prices["columns"].index(bus)] for row in prices["data"]]
        return (
            {
                "min_price": (min(series), _key(ws, f"Min price — {bus}")),
                "max_price": (max(series), _key(ws, f"Max price — {bus}")),
            },
            prices["index"],
            {},
        )
    if profile == "carrier-energy/1":
        rows = _get(client, "carrier_kpis")["rows"]
        gas = next(
            r for r in rows if (r["component"], r["carrier"]) == ("Generator", "gas")
        )
        return (
            {
                "carrier_generator_energy": (
                    gas["energy_mwh"],
                    _key(ws, "Generation — gas"),
                )
            },
            None,
            {},
        )
    if profile == "curtailment/1":
        plants = _get(client, "curtailment/by_cause")["plants"]
        assert list(plants) == [names["W1"]]  # the one variable generator
        wind = next(
            r for r in _get(client, "carrier_kpis")["rows"] if r["carrier"] == "wind"
        )
        index = _get(client, "curtailment")["index"]
        available = _column(ws, "Curtailment by cause", "available_mwh")
        return (
            {
                "curtailed_energy": (
                    plants[names["W1"]]["total_mwh"],
                    _key(ws, f"Curtailed energy — {names['W1']}"),
                ),
                "available_energy": (
                    plants[names["W1"]]["available_mwh"],
                    (available[0], "MWh"),
                ),
                "curtailment_share": (wind["curtailment_pct"], None),
            },
            index,
            {},
        )
    if profile == "network-emissions/1":
        emissions = _get(client, "emissions")
        # The route names an inactive cap where the checked answer names no_co2_cap.
        reasons = (
            {} if emissions["cap"]["active"] else {"co2_cap_shadow_price": "no_co2_cap"}
        )
        return (
            {"total_emissions": (emissions["total_tCO2"], _key(ws, "Total emissions"))},
            None,
            reasons,
        )
    shed = _get(client, "lost_load")
    return (
        {
            "lost_load_energy": (shed["total_mwh"], _key(ws, "Lost load")),
            "lost_load_cost": (shed["total_cost_eur"], _key(ws, "Lost-load cost")),
            "shed_hours": (
                shed["shed_hours"]["total"],
                _key(ws, "Shed hours (electrical buses)"),
            ),
            "peak_shed": (max(math.fsum(row) for row in shed["data"]), None),
        },
        shed["index"],
        {},
    )


def _assert_parity(record, surfaces, index, route_reasons):
    facts = {f["metric"]: f for f in record["facts"]}
    for metric, (route, cell) in surfaces.items():
        fact = facts[metric]
        assert route == fact["value"], (
            f"{metric}: route {route!r} != checked {fact['value']!r}"
        )
        if cell is not None:
            value, unit = cell
            assert value == fact["value"], (
                f"{metric}: workbook {value!r} != checked {fact['value']!r}"
            )
            assert unit == fact["unit"], (
                f"{metric}: workbook unit {unit!r} != checked {fact['unit']!r}"
            )
    window = record["window"]
    if index is not None:
        assert (window["start"], window["end"], window["snapshots"]) == (
            index[0],
            index[-1],
            len(index),
        ), f"window: route {index!r} != checked {window!r}"
    reasons = {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }
    assert reasons == route_reasons, (
        f"reasons: route {route_reasons!r} != checked {reasons!r}"
    )


@pytest.mark.parametrize("profile", PROFILES)
def test_checked_answer_route_and_workbook_agree_with_hand_arithmetic(
    solved, client, profile
):
    ctx, entity = solved(profile)
    record, values, reasons = _checked(ctx, profile, entity)
    assert values == HAND[profile]
    assert reasons == REASONS[profile]
    assert (
        record["window"]["start"],
        record["window"]["end"],
        record["window"]["snapshots"],
    ) == (STAMPS[0], STAMPS[-1], 3)
    surfaces, index, route_reasons = _surfaces(client, profile, PLAIN)
    _assert_parity(record, surfaces, index, route_reasons)
    for metric, (route, _cell) in surfaces.items():
        assert route == HAND[profile][metric]


def test_the_workbook_mean_price_carries_the_checked_unit(solved, client):
    """Ticket 22 (`_pending`): the cell's value is the plain mean, not the
    checked weighted mean, so only the unit, which holds before and after the
    fix, is asserted."""
    solved("bus-price/1")
    _value, unit = _key(_workbook(client, "bus-price/1"), "Mean price — B1")
    assert unit == "EUR/MWh"


def test_a_time_varying_efficiency_is_checked_as_the_lp_counts_it(solved, client):
    """Ticket 23 (`_pending`): the Emissions route and workbook divide by the
    static efficiency, the checked answer (and PyPSA's CO2 constraint) by the
    time-varying one. Only what agrees, and holds after the fix, is asserted."""

    def varying(n):
        n.generators_t.efficiency["G1"] = [0.5, 0.5, 0.25]

    ctx, entity = solved("network-emissions/1", tweak=varying)
    record, values, reasons = _checked(ctx, "network-emissions/1", entity)
    # G1 0.5*10/0.5*0.25 + 2*20/0.25*0.25 = 2.5 + 40; G2 2*12/0.4*0.25 = 15.
    assert values == {"total_emissions": 57.5}
    assert reasons == REASONS["network-emissions/1"]
    emissions = _get(client, "emissions")
    assert emissions["cap"]["active"] is False
    cell, unit = _key(_workbook(client, "network-emissions/1"), "Total emissions")
    assert unit == record["facts"][0]["unit"] == "tCO2"
    # The workbook reads the route's own service: they stay equal before and after the fix.
    assert cell == emissions["total_tCO2"]


# ── metamorphic fixtures ────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", PROFILES)
def test_doubled_weightings_double_energy_cost_and_hours_only(solved, profile):
    """×2 on every weighting: Σ 2w·x = 2·Σ w·x exactly (a power-of-two scale);
    means, minima, maxima, peaks and shares are unchanged."""
    ctx, entity = solved(profile, scale=2.0)
    _record, doubled, reasons = _checked(ctx, profile, entity)
    assert reasons == REASONS[profile]
    for metric, value in HAND[profile].items():
        expected = 2 * value if metric in ADDITIVE[profile] else value
        assert doubled[metric] == expected, metric


@pytest.mark.parametrize("profile", PROFILES)
def test_reordered_and_renamed_components_give_the_same_totals(solved, profile):
    """Generators added in reverse order and every bus, unit and load renamed.
    Every aggregate is math.fsum (or min/max) over exact LP values, so the
    totals are equal exactly, not within a ULP."""
    ctx, entity = solved(profile, names=RENAMED)
    _record, values, reasons = _checked(ctx, profile, entity)
    assert values == HAND[profile]
    assert reasons == REASONS[profile]


@pytest.mark.parametrize("profile", PROFILES)
def test_two_disjoint_windows_add_up_to_the_whole_run(solved, profile):
    """[t0] and [t1, t2]: additive metrics sum to the whole run; a whole-run
    minimum or maximum is the extreme of the windows' and bounds each."""
    ctx, entity = solved(profile)
    _, whole, _ = _checked(ctx, profile, entity)
    _, first, _ = _checked(ctx, profile, entity, (STAMPS[0], STAMPS[0]))
    _, rest, _ = _checked(ctx, profile, entity, (STAMPS[1], STAMPS[2]))
    for metric in ADDITIVE[profile]:
        # Every term is a dyadic float here, so the two partial sums add exactly.
        assert first[metric] + rest[metric] == whole[metric] == HAND[profile][metric], (
            metric
        )
    for metric in MAXIMA.get(profile, []):
        assert max(first[metric], rest[metric]) == whole[metric]
        assert first[metric] <= whole[metric] and rest[metric] <= whole[metric]
    for metric in MINIMA.get(profile, []):
        assert min(first[metric], rest[metric]) == whole[metric]
        assert first[metric] >= whole[metric] and rest[metric] >= whole[metric]


# ── injected wrong aggregates ───────────────────────────────────────────────


def _shift(module, name, change):
    """`module.name` returning its payload after `change(payload)`."""
    original = getattr(module, name)

    def shifted(*args, **kwargs):
        payload = original(*args, **kwargs)
        change(payload)
        return payload

    return shifted


def _plus_one_price(payload):
    payload["data"] = [[v + 1 for v in row] for row in payload["data"]]


def _plus_one_gas(payload):
    for row in payload["rows"]:
        if row["carrier"] == "gas":
            row["energy_mwh"] += 1


def _plus_one_curtailed(payload):
    for plant in payload["plants"].values():
        plant["total_mwh"] += 1


def _plus_one_tco2(payload):
    payload["total_tCO2"] += 1


def _plus_one_shed(payload):
    payload["total_mwh"] += 1


# (where the surface imports it from, service attribute, change); +1 in each unit.
INJECTIONS = {
    "bus-price/1": (
        ("routers.results", "services.results.prices"),
        "compute_prices",
        _plus_one_price,
    ),
    "carrier-energy/1": (("routers.results",), "compute_carrier_kpis", _plus_one_gas),
    "curtailment/1": (
        ("services.results.curtailment",),
        "curtailment_by_cause",
        _plus_one_curtailed,
    ),
    "network-emissions/1": (
        ("routers.results", "services.results.emissions"),
        "compute_emissions",
        _plus_one_tco2,
    ),
    "lost-load/1": (
        ("services.results.lost_load",),
        "lost_load_payload",
        _plus_one_shed,
    ),
}


@pytest.mark.parametrize("profile", PROFILES)
def test_an_injected_wrong_aggregate_fails_the_parity_check(
    solved, client, monkeypatch, profile
):
    """The service a surface reads is off by one unit. No surface refuses (none
    has a runtime cross-check); the parity check against the checked answer
    is what catches it."""
    import importlib

    ctx, entity = solved(profile)
    record, _values, _reasons = _checked(ctx, profile, entity)
    modules, name, change = INJECTIONS[profile]
    for module in modules:
        target = importlib.import_module(module)
        monkeypatch.setattr(target, name, _shift(target, name, change))
    surfaces, index, route_reasons = _surfaces(client, profile, PLAIN)
    with pytest.raises(AssertionError, match="route"):
        _assert_parity(record, surfaces, index, route_reasons)
