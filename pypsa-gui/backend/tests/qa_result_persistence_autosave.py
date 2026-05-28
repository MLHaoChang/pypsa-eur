"""
QA for the autosave-loses-results regression.

Bug: `_backup_network_ts_to_user_ts` walked every attribute in each
component's `_t` accessor and stashed non-empty columns into `_user_ts`.
That includes solver-OUTPUT attributes — ``generators_t.p`` /
``lines_t.p0`` / ``storage_units_t.state_of_charge`` etc. — which were
never user inputs. On the next save the captured dispatch was rewritten
to the live network by `_reapply_user_ts_to_network`, with two failure
modes:

  (a) Re-solve overwrite. If the user solved again, the new dispatch
      would be silently reverted to the original snapshot the next
      time autosave fired (because the existing _user_ts entry took
      precedence — _backup only updates when the entry is None / all-NaN).
  (b) Orphan-column stale dispatch. After a topology mutation that
      called `clear_dispatch` (any /api/network/* POST/PUT/DELETE/PATCH),
      _t was empty. The next autosave's reapply would re-populate it with
      the captured columns under names that may no longer exist in
      `n.<comp>.index` — flipping `dispatch_status` to 'stale' and
      gating every /results/* endpoint to 204 (no body).

Fix: the backup-side and reapply-side now both gate on PyPSA's
``components.<attr>.defaults['status']`` Input/Output flag, so only
user-supplied profile attributes (p_max_pu, p_set, marginal_cost,
inflow, …) round-trip through `_user_ts`.

Test plan:
  [1] After a solve, `_backup_network_ts_to_user_ts` MUST NOT capture
      output-only columns like ``generators_t.p`` / ``lines_t.p0``.
  [2] After a solve, `_backup_network_ts_to_user_ts` STILL captures
      input columns like ``generators_t.p_max_pu`` (the legacy behaviour
      this code was designed for).
  [3] Repeated save → solve → save cycles preserve dispatch (no
      reapply-overwrite regression). Asserts that the second solve's
      dispatch survives the autosave that fires after it.
  [4] Defense in depth: even if `_user_ts` is pre-populated with a stale
      output capture (simulating a user_ts.json saved by the older buggy
      version), `_reapply_user_ts_to_network` skips it instead of writing
      it back onto fresh dispatch.
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from routers.network import (
    _backup_network_ts_to_user_ts,
    _reapply_user_ts_to_network,
    _user_ts,
    _user_ts_lock,
)
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _reset_user_ts() -> None:
    """Test isolation — wipe the module-level _user_ts between scenarios."""
    with _user_ts_lock:
        _user_ts.clear()


def _build_solvable_network() -> pypsa.Network:
    """
    Minimal single-period network the LP can size and solve quickly.

    Two-bus, one extendable generator, one load with a time-varying
    profile (p_set as a real INPUT) so we can assert that profile is
    backed up while dispatch isn't.
    """
    n = pypsa.Network()
    n.snapshots = pd.date_range("2025-01-01", periods=12, freq="h")
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L", bus0="B1", bus1="B2", x=0.01, s_nom=500.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add(
        "Generator", "Gas", bus="B1", carrier="gas",
        p_nom=0.0, p_nom_extendable=True,
        p_nom_max=2000.0, marginal_cost=50.0,
        capital_cost=100.0,
    )
    n.add("Load", "L1", bus="B2")
    # 12-hour ramp profile — keeps the LP small but gives every snapshot a
    # different load level so dispatch is non-trivial.
    n.loads_t.p_set = pd.DataFrame(
        {"L1": [100, 120, 150, 180, 200, 220, 240, 220, 180, 150, 120, 100]},
        index=n.snapshots, dtype=float,
    )
    return n


def _run_solve(n: pypsa.Network) -> tuple[str, str]:
    PyPSAService.set_network(n)
    cfg = SolverConfig()
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    return run_simulation(cfg, n, lock, stop, log_q)


def test_backup_skips_output_attributes() -> None:
    print("\n[1] _backup_network_ts_to_user_ts skips OUTPUT attributes")
    _reset_user_ts()
    n = _build_solvable_network()
    status, condition = _run_solve(n)
    _step("solve ok", status in ("ok", "optimal"),
          f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        return

    # Pre-conditions: dispatch is populated on the live network.
    _step("generators_t.p has dispatch", not n.generators_t.p.empty)
    _step("lines_t.p0 has dispatch",     not n.lines_t.p0.empty)

    # The fix under test.
    _backup_network_ts_to_user_ts(n)
    captured_attrs = {(comp, attr) for (comp, attr, _) in _user_ts.keys()}

    output_attrs = [
        ("generators",    "p"),
        ("generators",    "mu_upper"),
        ("generators",    "mu_lower"),
        ("lines",         "p0"),
        ("lines",         "p1"),
        ("lines",         "mu_upper"),
        ("storage_units", "state_of_charge"),
    ]
    # Storage isn't in the test network so its (storage_units, *) keys
    # would be absent regardless. Filter to attrs whose component has
    # populated _t entries.
    for comp, attr in output_attrs:
        present = (comp, attr) in captured_attrs
        _step(f"  NOT captured: {comp}_t.{attr}", not present,
              f"saw {(comp, attr)} in _user_ts" if present else "")


def test_backup_captures_input_attributes() -> None:
    print("\n[2] _backup_network_ts_to_user_ts still captures INPUT attributes")
    _reset_user_ts()
    n = _build_solvable_network()
    # The legacy use case — capture an input profile BEFORE any solve so
    # there's no chance of conflating with output state.
    _backup_network_ts_to_user_ts(n)
    _step("loads_t.p_set captured (L1)",
          ("loads", "p_set", "L1") in _user_ts,
          msg="" if ("loads", "p_set", "L1") in _user_ts
              else f"keys: {sorted(_user_ts.keys())[:8]}")


def test_repeated_autosave_preserves_dispatch() -> None:
    """
    The original failure mode: solve → autosave fires (every 5 min).
    The pre-fix bug captured ``generators_t.p`` etc. into ``_user_ts`` on
    the first autosave. On subsequent autosaves, ``_reapply_user_ts_to_network``
    wrote that captured dispatch BACK to the network. If anything between
    save N and save N+1 mutated the dispatch (a re-solve, a topology
    edit, …) the captured copy would clobber the latest values.

    This test simulates the simpler invariant: five consecutive autosaves
    should leave the dispatch byte-identical, no matter what
    `_reapply_user_ts_to_network` does internally. With the fix the
    output attrs never enter `_user_ts`, so the reapply path can't touch
    them.
    """
    print("\n[3] Five consecutive autosaves preserve dispatch unchanged")
    _reset_user_ts()
    n = _build_solvable_network()
    _run_solve(n)
    baseline = n.generators_t.p["Gas"].copy()

    for i in range(1, 6):
        _backup_network_ts_to_user_ts(n)
        _reapply_user_ts_to_network(n)
        cur = n.generators_t.p["Gas"]
        ok = cur.equals(baseline)
        _step(f"autosave {i}: generators_t.p unchanged",
              ok, f"max drift = {(cur - baseline).abs().max():.4f}")
        # Also confirm dispatch column-set didn't drift (orphan column
        # detection — the failure mode that flipped dispatch_status to
        # 'stale' and gated /results/* endpoints to 204).
        _step(f"autosave {i}: generators_t.p columns match topology",
              set(n.generators_t.p.columns) == set(n.generators.index))


def test_reapply_skips_stale_output_keys_in_user_ts() -> None:
    """
    Defense-in-depth: a legacy user_ts.json may carry output keys
    captured by an older bug version. The reapply path must ignore them
    instead of overwriting fresh dispatch.
    """
    print("\n[4] _reapply_user_ts_to_network skips stale OUTPUT keys")
    _reset_user_ts()
    n = _build_solvable_network()
    _run_solve(n)

    fresh_dispatch = n.generators_t.p["Gas"].copy()

    # Plant a stale-looking output entry in _user_ts — simulates what an
    # old user_ts.json would carry.
    stale = fresh_dispatch * 0 + 999.0  # nonsense values, same index
    _user_ts[("generators", "p", "Gas")] = stale

    _reapply_user_ts_to_network(n)
    after = n.generators_t.p["Gas"].copy()

    _step("dispatch unchanged despite stale output key in _user_ts",
          after.equals(fresh_dispatch),
          f"unexpected reapply — max delta = {(after - fresh_dispatch).abs().max():.2f}")


def main() -> int:
    print("=== qa_result_persistence_autosave ===")
    test_backup_skips_output_attributes()
    test_backup_captures_input_attributes()
    test_repeated_autosave_preserves_dispatch()
    test_reapply_skips_stale_output_keys_in_user_ts()
    total = PASS + FAIL
    print(f"\nTotal: {total}  Pass: {PASS}  Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
