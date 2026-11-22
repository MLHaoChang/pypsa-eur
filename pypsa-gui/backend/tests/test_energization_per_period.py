# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02b: a staged template's connection limit per investment period,
the energization schedule ``links_t["eh_energization_mw"]``. One writer
(`energization.set_period_mw`, behind ``PUT /api/projects/{name}/flow/energization``)
sets one period flat; a period that varies is refused with its range, never
scaled (GK A W1-W6, GK B E1-E7)."""

from __future__ import annotations

import json
import pathlib
import threading
import types

from decimal import Decimal

import numpy as np
import pandas as pd
import pypsa
import pytest
from pandas.testing import assert_frame_equal

from project_templates.eh_templates import (
    build_eh_datacenter,
    build_eh_datacenter_staged,
)
from services.adequacy import energization as E

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
LINK = "grid_import"


def _staged():
    return build_eh_datacenter_staged()


def _single(values=(20.0,) * 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(values), freq="h"))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Link", LINK, bus0="grid", bus1="site", p_nom=40.0)
    n.add("Link", "other", bus0="grid", bus1="site", p_nom=10.0)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: list(values)}, index=n.snapshots)
    return n


def _refused(n, kind, link, period, mw):
    before = E.energization_frame(n)
    before = None if before is None else before.copy()
    with pytest.raises(E.EnergizationEditError) as exc:
        E.set_period_mw(n, link, period, mw)
    assert exc.value.error_kind == kind
    after = E.energization_frame(n)
    if before is None:
        assert after is None  # never created (W3)
    else:
        assert_frame_equal(after, before)  # W4: untouched
    return exc.value.message


# ── the writer ───────────────────────────────────────────────────────────────


def test_one_period_is_set_flat_and_no_other_moves():
    n = _staged()
    frame = E.energization_frame(n)
    before = frame.copy()
    out = E.set_period_mw(n, LINK, 2030, 30.0)
    assert out["period"] == "2030" and out["mw"] == 30.0 and out["previous_mw"] == 55.0
    assert (
        out["note"] is None
        and len(out["series"]) == (frame.index.get_level_values(0) == 2030).sum()
    )
    after = E.energization_frame(n)
    assert after is frame  # edited in place
    assert after.index.equals(before.index) and after.dtypes.equals(before.dtypes)
    assert (after.loc[2030, LINK] == 30.0).all()
    for p in (2027, 2035):
        assert_frame_equal(after.loc[[p]], before.loc[[p]])  # not every period
    assert E.by_period(n, LINK) == {
        "by_period": {"2027": 15.0, "2030": 30.0, "2035": 55.0}
    }


def test_the_period_may_be_given_as_its_digits():
    n = _staged()
    assert E.set_period_mw(n, LINK, "2035", 40)["period"] == "2035"
    assert (E.energization_frame(n).loc[2035, LINK] == 40.0).all()


@pytest.mark.parametrize("mw", [True, False, "30", None, [30.0]])
def test_a_value_that_is_not_a_number_is_refused(mw):
    assert (
        _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw) == E.NOT_A_NUMBER
    )


@pytest.mark.parametrize(
    "mw", [float("nan"), float("inf"), -float("inf"), np.float64("nan")]
)
def test_a_value_that_is_not_finite_is_refused(mw):
    assert (
        _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw) == E.NOT_A_NUMBER
    )


@pytest.mark.parametrize("mw", [0, 0.0, -5.0])
def test_a_value_not_above_zero_is_refused(mw):
    assert (
        _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw)
        == E.NOT_ABOVE_ZERO
    )


def test_a_numpy_number_is_accepted():
    n = _staged()
    assert E.set_period_mw(n, LINK, 2030, np.float32(12.5))["mw"] == 12.5


def test_a_link_without_a_schedule_is_refused_and_none_is_created():
    n = _staged()
    other = next(link for link in n.links.index if link != LINK)
    _refused(n, "energization_no_schedule", other, 2030, 10.0)
    assert other not in E.energization_frame(n).columns
    _refused(n, "energization_no_schedule", "nope", 2030, 10.0)
    plain = build_eh_datacenter()  # no schedule at all
    assert E.energization_frame(plain) is None
    _refused(plain, "energization_no_schedule", LINK, "all", 10.0)
    assert E.ATTR not in plain.links_t or E.energization_frame(plain) is None
    # The row reads nothing for either.
    assert E.by_period(n, other) is None and E.by_period(plain, LINK) is None
    assert E.all_by_period(plain) == {}


def test_a_column_for_a_link_the_network_lacks_is_refused():
    n = _single()
    n.links_t[E.ATTR]["ghost"] = 10.0
    _refused(n, "energization_no_schedule", "ghost", "all", 5.0)


def test_the_schedule_digest_reads_none_without_a_value():
    n = _single((np.nan,) * 4)
    assert E.schedule_digest(n) is None
    assert E.schedule_digest(_single()) is not None


def test_a_column_not_stored_as_numbers_is_refused():
    n = _single()
    n.links_t[E.ATTR] = n.links_t[E.ATTR].astype(object)
    _refused(n, "energization_no_schedule", LINK, "all", 10.0)


@pytest.mark.parametrize("period", [2031, "2031", None, "all", True, 2030.0])
def test_a_period_not_in_level_zero_is_refused(period):
    message = _refused(_staged(), "energization_unknown_period", LINK, period, 10.0)
    assert "2027, 2030, 2035" in message


@pytest.mark.parametrize("period", [2030.0, True, Decimal("2030")])
def test_g1_a_float_or_bool_period_never_writes_2030(period):
    """GK G1: the period-type check on a real staged network, whose level 0
    is int64 (so its keys are digit strings: "2030", never "2030.0" or
    "True"). A float 2030.0 or a bool is refused, typed, and 2030 keeps 55 MW."""
    n = _staged()
    frame = E.energization_frame(n)
    assert frame.index.get_level_values(0).dtype == np.int64
    before = frame.copy()
    with pytest.raises(E.EnergizationEditError) as exc:
        E.set_period_mw(n, LINK, period, 10.0)
    assert exc.value.error_kind == "energization_unknown_period"
    assert_frame_equal(E.energization_frame(n), before)
    assert (E.energization_frame(n).loc[2030, LINK] == 55.0).all()


def test_g1_a_numpy_int_period_from_the_index_is_taken():
    """A period read from the frame's own level 0 is np.int64: it sets that period."""
    n = _staged()
    period = E.energization_frame(n).index.get_level_values(0)[-1]
    assert isinstance(period, np.integer) and not isinstance(period, int)
    out = E.set_period_mw(n, LINK, period, 33.0)
    assert out["period"] == str(period)
    assert (E.energization_frame(n).loc[int(period), LINK] == 33.0).all()


@pytest.mark.parametrize("period", [2030, "2030", 0])
def test_a_single_axis_takes_only_all(period):
    _refused(_single(), "energization_unknown_period", LINK, period, 10.0)


@pytest.mark.parametrize("period", ["all", None])
def test_a_single_axis_sets_all(period):
    n = _single()
    out = E.set_period_mw(n, LINK, period, 25.0)
    assert out["period"] == "all" and out["previous_mw"] == 20.0
    assert (E.energization_frame(n)[LINK] == 25.0).all()


def test_a_period_that_varies_is_refused_with_its_range_never_scaled():
    """E7 / W6 (non-flat check)."""
    n = _staged()
    frame = E.energization_frame(n)
    rows = np.flatnonzero(frame.index.get_level_values(0) == 2035)
    frame.iloc[rows[:10], 0] = 30.0
    frame.iloc[rows[10:], 0] = 40.0
    assert (
        _refused(n, "energization_period_not_flat", LINK, 2035, 50.0)
        == "2035 varies 30–40 MW: set it on the network."
    )
    assert E.by_period(n, LINK)["by_period"]["2035"] == {"min": 30.0, "max": 40.0}
    # The other periods stay editable.
    assert E.set_period_mw(n, LINK, 2030, 20.0)["mw"] == 20.0


def test_a_period_with_hours_without_a_value_is_not_flat():
    n = _single((20.0, np.nan, 20.0, 20.0))
    assert (
        _refused(n, "energization_period_not_flat", LINK, "all", 10.0)
        == "The period has hours without a value (20 MW in the others): set it on the network."
    )
    assert E.by_period(n, LINK) == {"by_period": {"all": {"min": 20.0, "max": 20.0}}}


def test_a_period_without_any_value_is_set():
    n = _single((np.nan,) * 4)
    assert E.by_period(n, LINK) == {"by_period": {"all": None}}
    assert E.set_period_mw(n, LINK, "all", 10.0)["previous_mw"] is None


def test_above_the_rating_is_accepted_with_its_note():
    """W5: the per-period value does not cap or raise the link's p_nom."""
    n = _staged()
    p_nom = float(n.links.at[LINK, "p_nom"])
    out = E.set_period_mw(n, LINK, 2030, p_nom + 10)
    assert out["mw"] == p_nom + 10 and out["p_nom_mw"] == p_nom
    assert (
        out["note"]
        == f"This is above the link's rating ({p_nom:g} MW): it doesn't raise the link's capacity."
    )
    assert float(n.links.at[LINK, "p_nom"]) == p_nom
    assert E.set_period_mw(n, LINK, 2030, p_nom)["note"] is None


# ── W1: the input fingerprint ────────────────────────────────────────────────


def _fp_ctx(n):
    return types.SimpleNamespace(
        network=n, solver_state={"solver_config": {}}, user_ts={}, storage_dir=None
    )


def _fps(n):
    from services.workflow_tools import _network_input_fingerprint

    ctx = _fp_ctx(n)
    return _network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)


def test_the_fingerprint_moves_on_a_period_edit_both_branches():
    n = _staged()
    before = _fps(n)
    assert _fps(n) == before  # deterministic
    E.set_period_mw(n, LINK, 2030, 30.0)
    after = _fps(n)
    assert after[0] != before[0] and after[1] != before[1]


def test_the_fingerprint_moves_on_an_upload_equivalent_write():
    """The gap 02b fixes: a schedule written whole (an upload, a netCDF
    import) left the fingerprint as it was."""
    n = _staged()
    before = _fps(n)
    frame = E.energization_frame(n)
    n.links_t[E.ATTR] = pd.DataFrame(
        {LINK: frame[LINK].to_numpy() * 0.5}, index=frame.index
    )
    after = _fps(n)
    assert after[0] != before[0] and after[1] != before[1]


# GK A 32 (W1/AS-02): master's fingerprints, computed by `_network_input_fingerprint`
# on origin/master (no energization block), (plain, exact). A literal, so a block
# appended on every network (its marker moved above the `if`) is seen.
MASTER_FP_SINGLE = (
    "5d957d5904040a2461e135cab361dcc37c94837dfdb4281f1a49a835d206702c",
    "c5d6c416f41b319c4043f2db0c30def9f22e9273ded7664a958c355bcda81433",
)
MASTER_FP_EH_DATACENTER = (
    "4ae621820bfa2bbdf0295d5fe6d9557ed89025a5197cd4826898bf550e13a0bd",
    "9766df8fdb39a263d938f2b2ded7d83f52be73968322a43128d6389b0715141b",
)


def test_the_fingerprint_is_unchanged_without_the_column():
    """Byte-identical to master: a network without a schedule (or an all-NaN
    one) hashes as it did before the block existed."""
    n = _single()
    with_schedule = _fps(n)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: [np.nan] * 4}, index=n.snapshots)
    nan_only = _fps(n)
    del n.links_t[E.ATTR]
    without = _fps(n)
    assert nan_only == without == MASTER_FP_SINGLE != with_schedule


def test_g3_a_partly_nan_column_is_hashed_and_moves_when_its_nan_period_is_set():
    """GK G3: a column with a value in only some periods is in the block (its
    fingerprint differs from no schedule at all), and setting the NaN period
    moves it."""
    n = _staged()
    frame = E.energization_frame(n)
    frame.loc[2035, LINK] = np.nan  # a value in 2027 and 2030 only
    partly_nan = _fps(n)
    del n.links_t[E.ATTR]
    without = _fps(n)
    n.links_t[E.ATTR] = frame
    assert _fps(n) == partly_nan != without
    assert E.set_period_mw(n, LINK, 2035, 40.0)["previous_mw"] is None
    after = _fps(n)
    assert after[0] != partly_nan[0] and after[1] != partly_nan[1]


def _two_links():
    """The staged network with a second scheduled Link: ``grid_import`` has no
    value in 2035, ``import_b`` has one in every period."""
    n = _staged()
    n.add(
        "Link",
        "import_b",
        bus0=n.links.at[LINK, "bus0"],
        bus1=n.links.at[LINK, "bus1"],
        p_nom=30.0,
    )
    frame = E.energization_frame(n).copy()
    frame["import_b"] = 25.0
    frame.loc[2035, LINK] = np.nan
    n.links_t[E.ATTR] = frame
    return n


@pytest.mark.parametrize(
    "link, period", [("import_b", 2035), (LINK, 2035), ("import_b", 2030)]
)
def test_g3_two_links_one_partly_nan_each_edit_moves_the_fingerprint(link, period):
    """GK A 32 G3: a NaN in one column drops no row of the other. Setting
    ``import_b`` in 2035, where ``grid_import`` has no value, moves the
    fingerprint and the investment case's digest (a block hashed after
    ``dropna()`` would not see it)."""
    n = _two_links()
    before, digest = _fps(n), E.schedule_digest(n)
    E.set_period_mw(n, link, period, 12.0)
    after = _fps(n)
    assert after[0] != before[0] and after[1] != before[1]
    assert E.schedule_digest(n) != digest


# ── W2: the investment case's key ───────────────────────────────────────────


def _ic_key(n):
    from routers import results

    cfg = types.SimpleNamespace(finance=None, commercial=None)
    return results._ic_assumptions(cfg, n, lock=threading.RLock())


def test_the_investment_case_key_moves_with_the_schedule():
    n = _staged()
    h0, parts = _ic_key(n)
    assert parts["energization"] == E.schedule_digest(n)
    E.set_period_mw(n, LINK, 2030, 30.0)
    h1, parts1 = _ic_key(n)
    assert h1 != h0 and parts1["energization"] != parts["energization"]
    frame = E.energization_frame(n)
    n.links_t[E.ATTR] = pd.DataFrame(
        {LINK: frame[LINK].to_numpy() * 0.5}, index=frame.index
    )
    assert _ic_key(n)[0] != h1


def test_the_investment_case_key_is_unchanged_without_a_schedule():
    from services.finance.investment_case_runner import assumptions_digest

    n = build_eh_datacenter()
    h, parts = _ic_key(n)
    assert "energization" not in parts
    base = dict(finance=None, commercial=None, dispatch="d", packs={})
    assert assumptions_digest(**base) == assumptions_digest(**base, energization=None)
    assert (
        assumptions_digest(**base)[0] != assumptions_digest(**base, energization="x")[0]
    )


class _EditOnEntry:
    """A lock with an edit of the schedule landing just as the reader enters
    it (GK A 32 G2, mutant M17); ``held`` is the reader's depth inside it."""

    def __init__(self, lock, edit):
        self._lock, self._edit, self.entries, self.held = lock, edit, 0, 0

    def acquire(self, *a, **kw):
        return self._lock.acquire(*a, **kw)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.entries += 1
        if self.entries == 1:
            self._edit()
        entered = self._lock.__enter__()
        self.held += 1
        return entered

    def __exit__(self, *exc):
        self.held -= 1
        return self._lock.__exit__(*exc)


def test_g2_the_investment_case_reads_the_schedule_under_its_lock(monkeypatch):
    from routers import results

    n = _staged()
    lock = _EditOnEntry(threading.RLock(), lambda: E.set_period_mw(n, LINK, 2030, 30.0))
    real, held = E.schedule_digest, []

    def digest(network):
        held.append(lock.held)
        return real(network)

    monkeypatch.setattr(E, "schedule_digest", digest)
    cfg = types.SimpleNamespace(finance=None, commercial=None)
    _, parts = results._ic_assumptions(cfg, n, lock=lock)
    assert lock.entries >= 1, "the case never entered the lock"
    assert held == [1], "the schedule was read outside the lock"
    assert parts["energization"] == real(n)  # the edit that landed at entry


# GK B D2a / D2b: the investment case's status across the update. A record
# made before 02b holds the parts and the hash without `energization`.

PRE_02B_PARTS = {
    "finance",
    "value_flows",
    "commercial",
    "solver_config",
    "dispatch",
    "packs",
    "owner_assets",
    "extra_assets",
}


def _ic_stored(key):
    from services.finance.report import refused_finance_report

    h, parts = key
    stored = refused_finance_report("x", assumptions_hash=h).model_dump(mode="json")
    stored["sections"]["project"]["payload"] = {"provenance": {"inputs": dict(parts)}}
    return stored


def _pre_02b(key):
    """The key the code before 02b computed from the same inputs."""
    from services.finance.investment_case_runner import json_digest

    parts = {k: v for k, v in key[1].items() if k != "energization"}
    return json_digest(parts), parts


def test_d2a_an_unscheduled_network_keeps_its_pre_02b_key_and_reads_fresh():
    from services.finance.investment_case_runner import staleness

    n = build_eh_datacenter()  # the real template, no schedule
    assert E.energization_frame(n) is None
    key = _ic_key(n)
    assert set(key[1]) == PRE_02B_PARTS
    assert key == _pre_02b(key)  # byte-identical hash and parts
    status = staleness(_ic_stored(_pre_02b(key)), _ic_key(n))
    assert status["stale"] is False and status["reason"] is None
    assert status["changed"] == []
    # The study fingerprint too: the block adds nothing on this network.
    assert E.schedule_digest(n) is None


def test_d2a_the_fingerprint_of_an_unscheduled_template_is_pre_02b():
    n = build_eh_datacenter()
    assert E.energization_frame(n) is None
    assert _fps(n) == MASTER_FP_EH_DATACENTER  # master's, byte for byte


def test_d2b_a_scheduled_network_reads_inputs_changed_once_then_fresh(monkeypatch):
    from services.finance.investment_case_runner import staleness

    n = _staged()  # the real template, carrying its schedule
    stored = _ic_stored(_pre_02b(_ic_key(n)))
    status = staleness(stored, _ic_key(n))
    assert status["stale"] is True and status["reason"] == "inputs_changed"
    assert status["changed"] == ["energization"]  # named, nothing else
    stored = _ic_stored(_ic_key(n))  # the case re-run records the new key
    for _ in range(2):  # and stays fresh: stale exactly once
        status = staleness(stored, _ic_key(n))
        assert status["stale"] is False and status["reason"] is None
    # The study fingerprint: a solve recorded before 02b reads changed once,
    # and a re-solve (re-record) reads fresh.
    with_block = _fps(n)
    with monkeypatch.context() as m:
        m.setattr(E, "energization_frame", lambda _n: None)
        recorded = _fps(n)
    assert recorded[0] != with_block[0] and recorded[1] != with_block[1]
    recorded = _fps(n)
    assert recorded == with_block and _fps(n) == recorded


# ── E4: the run's freeze and admission ──────────────────────────────────────


def _run_ctx(tmp_path, n, template="eh_datacenter_staged"):
    from services import flow_service as F

    meta = json.loads((TEMPLATES / template / "eh_template.json").read_text())
    (tmp_path / F.TEMPLATE_FILE).write_text(json.dumps(meta))
    return types.SimpleNamespace(
        storage_dir=str(tmp_path),
        project_uuid="p",
        network=n,
        network_revision=0,
        solver_state_lock=threading.RLock(),
        solver_state={"solver_config": {}},
    )


def _admit(tmp_path, ctx, frozen):
    from harness import run_eh

    step = {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": dict(frozen["args"]),
        "frozen": frozen,
        "status": "pending",
        "attempt_id": "a1",
    }
    task = {
        "task_id": "t1",
        "project_id": "p",
        "status": "running",
        "steps": [step],
        "run": {
            "profile": "eh_site@1",
            "status": "running",
            "stage": "solve",
            "stages": {},
        },
    }
    return run_eh.admit(tmp_path / "task.json", task, step, step["args"], ctx)


def test_freeze_records_the_schedule_and_admit_compares_it_exactly(tmp_path):
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    assert frozen["energization"] == {
        "digest": E.schedule_digest(n),
        "by_period": {LINK: {"by_period": {"2027": 15.0, "2030": 55.0, "2035": 55.0}}},
    }
    assert run_eh._energization_change(ctx, frozen) is None
    E.set_period_mw(n, LINK, 2030, 54.999999)  # exact, never a tolerance
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule changed since the study's card "
        "was drawn (grid_import). Review the run again."
    )


def test_a_record_frozen_before_02b_refuses_on_a_staged_network(tmp_path):
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    frozen.pop("energization")
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND and message == run_eh.ENERGIZATION_UNRECORDED
    # A network without a schedule has nothing to re-freeze.
    plain = types.SimpleNamespace(network=build_eh_datacenter())
    assert run_eh._energization_change(plain, {"args": {}}) is None


def test_d4_a_record_frozen_before_02b_admits_on_an_unscheduled_network(tmp_path):
    from harness import run_eh

    ctx = _run_ctx(tmp_path, build_eh_datacenter(), "eh_datacenter")
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    # GK B E4: a non-staged card is byte-equal to a pre-02b one (no entry).
    assert "energization" not in frozen
    assert _admit(tmp_path, ctx, frozen) is None  # admitted, as before 02b


@pytest.mark.parametrize(
    "record",
    [{"by_period": {}}, {}],  # no digest; {} is the unmerged pre-digest shape
)
def test_d4_a_digest_less_record_refuses_on_an_unscheduled_network(tmp_path, record):
    """GK B 68 (D5 survivor, `"digest" not in then`): a record that carries an
    energization entry but no digest is not a pre-02b record (that has no
    entry at all, and admits above). It can't be read, so it refuses even
    where the network has no schedule: its missing digest is never "None"."""
    from harness import run_eh

    ctx = _run_ctx(tmp_path, build_eh_datacenter(), "eh_datacenter")
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    frozen["energization"] = record
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule can't be compared now. "
        "Review the run again."
    )


def test_e4_a_ramp_reversed_inside_a_period_refuses(tmp_path):
    """GK A 32 E4: 2027 as a ramp, frozen, then reversed. Its min and max (the
    flow row's reading) are the same; the schedule, the LP input and the
    fingerprint are not, so admission refuses."""
    from harness import run_eh

    n = _staged()
    frame = E.energization_frame(n)
    rows = np.flatnonzero(frame.index.get_level_values(0) == 2027)
    frame.iloc[rows, 0] = np.linspace(10.0, 20.0, rows.size)
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    summary = E.all_by_period(n)
    ramp = frame.iloc[rows, 0].to_numpy()
    frame.iloc[rows, 0] = ramp[::-1]  # the same values, exactly, in reverse
    assert sorted(frame.iloc[rows, 0]) == sorted(
        ramp
    )  # an order-blind digest can't see it
    assert E.all_by_period(n) == summary  # the summary cannot see it
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule changed since the study's card "
        "was drawn (inside a period). Review the run again."
    )


def test_e4_a_schedule_unreadable_at_freeze_and_admit_cannot_be_compared(
    tmp_path, monkeypatch
):
    """GK A 32 (mutant M21): unreadable at both ends is never "unchanged"."""
    from harness import run_eh

    def unreadable(_n):
        raise RuntimeError("unreadable")

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    monkeypatch.setattr(E, "all_by_period", unreadable)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    assert frozen["energization"] is None
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule can't be compared now. "
        "Review the run again."
    )


def test_e4_a_schedule_unreadable_only_at_admit_cannot_be_compared(
    tmp_path, monkeypatch
):
    """D5 survivor (`now is None`): frozen readable, unreadable at admission.
    It refuses with a sentence; it never raises out of admission."""
    from harness import run_eh

    def unreadable(_n):
        raise RuntimeError("unreadable")

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    monkeypatch.setattr(E, "all_by_period", unreadable)
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule can't be compared now. "
        "Review the run again."
    )


def test_d4_a_pre_02b_record_refuses_where_the_schedule_is_unreadable(
    tmp_path, monkeypatch
):
    """D5 survivor (`now is not None and …`): a record frozen before 02b, and
    a schedule that can't be read now, is never read as "no schedule"."""
    from harness import run_eh

    def unreadable(_n):
        raise RuntimeError("unreadable")

    ctx = _run_ctx(tmp_path, build_eh_datacenter(), "eh_datacenter")
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    assert "energization" not in frozen  # pre-02b shape, as a non-staged card
    monkeypatch.setattr(E, "all_by_period", unreadable)
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND and message == run_eh.ENERGIZATION_UNRECORDED


def test_e4_a_record_without_by_period_still_refuses_a_changed_digest(tmp_path):
    """D5 survivor (`then.get("by_period") or {}`): a record whose digest no
    longer matches refuses even when it carries no by_period; the links named
    are then those the network schedules now."""
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    frozen["energization"] = {"digest": "not-this-schedule"}
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == (
        "The connection's energization schedule changed since the study's card "
        "was drawn (grid_import). Review the run again."
    )


def test_e4_a_renamed_link_column_with_the_same_values_refuses(tmp_path):
    """GK A 33 E4: the schedule moved to another link, every value the same.
    A digest without the column names would read it unchanged."""
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(
        json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6))
    )
    frame = E.energization_frame(n)
    other = next(link for link in n.links.index if link != LINK)
    n.links_t[E.ATTR] = frame.rename(columns={LINK: other})
    assert E.energization_frame(n)[other].equals(frame[LINK])  # same values
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message.startswith(
        "The connection's energization schedule changed since the study's card was drawn ("
    )
    assert LINK in message
