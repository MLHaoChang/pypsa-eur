"""
QA test: verify Line/Transformer get single-extendable treatment in
vintage_service, while Generator/StorageUnit still get per-period vintage
rows. Synthetic 3-bus 3-period network — no LP solve, just inspect the
post-apply network state.

Run: cd backend && ../../.pixi/envs/default/python.exe tests/qa_single_extendable_line.py
"""
import math
import os
import sys

# Force stdout to UTF-8 so phase log lines containing arrows / unicode
# from vintage_service don't blow up on cp1252 Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pypsa
from services.vintage_service import apply_vintage_bounds, set_bounds_for_asset


def build_test_network() -> pypsa.Network:
    """
    3-bus radial multi-period network with:
    • 1 generator (Solar) with per-period vintage bounds
    • 1 storage (Battery) with per-period vintage bounds
    • 1 line (Bus1-Bus2) with per-period vintage bounds
    • 1 transformer (Bus2-Bus3) with per-period vintage bounds
    """
    n = pypsa.Network()
    periods = [2026, 2027, 2028]
    base_idx = pd.date_range("2026-01-01", periods=24, freq="h")
    mi = pd.MultiIndex.from_arrays(
        [[p for p in periods for _ in range(len(base_idx))],
         list(base_idx) * len(periods)],
        names=["period", "timestep"],
    )
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = periods

    n.add("Bus", "Bus1", v_nom=380)
    n.add("Bus", "Bus2", v_nom=380)
    n.add("Bus", "Bus3", v_nom=380)

    n.add("Line", "Bus1-Bus2", bus0="Bus1", bus1="Bus2",
          s_nom=100, x=0.05, r=0.01, build_year=2026, lifetime=25)

    n.add("Transformer", "Bus2-Bus3", bus0="Bus2", bus1="Bus3",
          s_nom=50, x=0.08, r=0.02, build_year=2026, lifetime=25)

    n.add("Generator", "Solar", bus="Bus1", carrier="solar",
          p_nom=0, marginal_cost=0,
          overnight_cost=500000, lifetime=25, build_year=2026)

    n.add("StorageUnit", "Battery", bus="Bus2", carrier="battery",
          p_nom=0, max_hours=4,
          overnight_cost=300000, lifetime=15, build_year=2026)

    n.add("Load", "load1", bus="Bus3", p_set=50)

    # Set per-period vintage bounds for ALL four asset types.
    set_bounds_for_asset(n, "Line", "Bus1-Bus2", {
        "2026": {"p_nom_min": 0, "p_nom_max": 50},
        "2027": {"p_nom_min": 0, "p_nom_max": 50},
        "2028": {"p_nom_min": 0, "p_nom_max": 50},
    })
    set_bounds_for_asset(n, "Transformer", "Bus2-Bus3", {
        "2026": {"p_nom_min": 0, "p_nom_max": 30},
        "2028": {"p_nom_min": 0, "p_nom_max": 70},
    })
    set_bounds_for_asset(n, "Generator", "Solar", {
        "2026": {"p_nom_min": 0, "p_nom_max": 100},
        "2027": {"p_nom_min": 0, "p_nom_max": 50},
        "2028": {"p_nom_min": 0, "p_nom_max": 50},
    })
    set_bounds_for_asset(n, "StorageUnit", "Battery", {
        "2026": {"p_nom_min": 0, "p_nom_max": 80},
        "2027": {"p_nom_min": 0, "p_nom_max": 40},
    })
    return n


def run_qa():
    n = build_test_network()
    n_lines_before = len(n.lines)
    n_transformers_before = len(n.transformers)
    n_gens_before = len(n.generators)
    n_storage_before = len(n.storage_units)

    undo_actions: list = []
    phase_lines: list[str] = []
    def phase(msg):
        phase_lines.append(msg)

    total_added = apply_vintage_bounds(n, undo_actions, phase)

    print("=" * 80)
    print(" QA TEST: single-extendable lines vs per-period vintages")
    print("=" * 80)
    print(f"\napply_vintage_bounds reported {total_added} expanded asset(s).")
    print("Phase log:")
    for line in phase_lines:
        print(f"   {line}")

    print("\n--- LINES ---")
    print(f"  Count before: {n_lines_before}, after: {len(n.lines)}")
    print(f"  Line names: {list(n.lines.index)}")
    for ln in n.lines.index:
        ext = n.lines.at[ln, "s_nom_extendable"]
        s_nom = n.lines.at[ln, "s_nom"]
        s_min = n.lines.at[ln, "s_nom_min"]
        s_max = n.lines.at[ln, "s_nom_max"]
        print(f"  {ln}: s_nom={s_nom}, ext={ext}, [s_nom_min={s_min}, s_nom_max={s_max}]")

    print("\n--- TRANSFORMERS ---")
    print(f"  Count before: {n_transformers_before}, after: {len(n.transformers)}")
    print(f"  Transformer names: {list(n.transformers.index)}")
    for tr in n.transformers.index:
        ext = n.transformers.at[tr, "s_nom_extendable"]
        s_nom = n.transformers.at[tr, "s_nom"]
        s_min = n.transformers.at[tr, "s_nom_min"]
        s_max = n.transformers.at[tr, "s_nom_max"]
        print(f"  {tr}: s_nom={s_nom}, ext={ext}, [s_nom_min={s_min}, s_nom_max={s_max}]")

    print("\n--- GENERATORS ---")
    print(f"  Count before: {n_gens_before}, after: {len(n.generators)}")
    print(f"  Generator names: {list(n.generators.index)}")
    for g in n.generators.index:
        ext = n.generators.at[g, "p_nom_extendable"]
        p_nom = n.generators.at[g, "p_nom"]
        by = n.generators.at[g, "build_year"]
        print(f"  {g}: p_nom={p_nom}, ext={ext}, build_year={by}")

    print("\n--- STORAGE UNITS ---")
    print(f"  Count before: {n_storage_before}, after: {len(n.storage_units)}")
    print(f"  StorageUnit names: {list(n.storage_units.index)}")
    for s in n.storage_units.index:
        ext = n.storage_units.at[s, "p_nom_extendable"]
        p_nom = n.storage_units.at[s, "p_nom"]
        by = n.storage_units.at[s, "build_year"]
        print(f"  {s}: p_nom={p_nom}, ext={ext}, build_year={by}")

    print("\n" + "=" * 80)
    print(" ASSERTIONS")
    print("=" * 80)

    # Lines: SHOULD remain 1 (no vintage rows added). Parent flipped to extendable.
    failures = []
    if len(n.lines) != n_lines_before:
        failures.append(
            f"Lines: expected {n_lines_before} (no vintages), got {len(n.lines)}"
        )
    else:
        print(f"  [OK]Lines unchanged (no vintage rows added): {len(n.lines)} == {n_lines_before}")

    parent_line = "Bus1-Bus2"
    if parent_line in n.lines.index:
        if not bool(n.lines.at[parent_line, "s_nom_extendable"]):
            failures.append(f"Line '{parent_line}' should be extendable but isn't")
        else:
            print(f"  [OK]Parent line '{parent_line}' is now extendable")
        # Expected s_nom_max = parent.s_nom + Σ period max = 100 + 50*3 = 250
        expected_max = 100 + 50 * 3
        actual_max = float(n.lines.at[parent_line, "s_nom_max"])
        if not math.isclose(actual_max, expected_max):
            failures.append(
                f"Line '{parent_line}' s_nom_max: expected {expected_max}, got {actual_max}"
            )
        else:
            print(f"  [OK]Line '{parent_line}' s_nom_max = parent(100) + Σ(50+50+50) = {actual_max}")

    # Transformers: same — no vintages, parent extendable.
    if len(n.transformers) != n_transformers_before:
        failures.append(
            f"Transformers: expected {n_transformers_before}, got {len(n.transformers)}"
        )
    else:
        print(f"  [OK]Transformers unchanged (no vintage rows added): {len(n.transformers)} == {n_transformers_before}")
    parent_trafo = "Bus2-Bus3"
    if parent_trafo in n.transformers.index:
        if not bool(n.transformers.at[parent_trafo, "s_nom_extendable"]):
            failures.append(f"Transformer '{parent_trafo}' should be extendable but isn't")
        else:
            print(f"  [OK]Parent transformer '{parent_trafo}' is now extendable")
        # Expected s_nom_max = 50 + (30 + 0 + 70) = 150 (2027 has no bound → 0)
        expected_max = 50 + 30 + 70
        actual_max = float(n.transformers.at[parent_trafo, "s_nom_max"])
        if not math.isclose(actual_max, expected_max):
            failures.append(
                f"Transformer '{parent_trafo}' s_nom_max: expected {expected_max}, got {actual_max}"
            )
        else:
            print(f"  [OK]Transformer '{parent_trafo}' s_nom_max = 50 + (30+70) = {actual_max}")

    # Generators: vintage rows SHOULD have been added (one per period with bounds).
    expected_gen_count = n_gens_before + 3  # Solar has bounds in 3 periods
    if len(n.generators) != expected_gen_count:
        failures.append(
            f"Generators: expected {expected_gen_count} (parent + 3 vintages), got {len(n.generators)}"
        )
    else:
        print(f"  [OK]Generators expanded into vintages: 1 parent + 3 vintages = {len(n.generators)}")
    for period in (2026, 2027, 2028):
        v_name = f"Solar@{period}"
        if v_name not in n.generators.index:
            failures.append(f"Expected generator vintage '{v_name}' missing")
        elif not bool(n.generators.at[v_name, "p_nom_extendable"]):
            failures.append(f"Generator vintage '{v_name}' should be extendable")
        else:
            print(f"  [OK]Generator vintage '{v_name}' exists and is extendable")

    # StorageUnits: vintages should exist for 2026, 2027 (not 2028 — no bounds).
    expected_su_count = n_storage_before + 2
    if len(n.storage_units) != expected_su_count:
        failures.append(
            f"StorageUnits: expected {expected_su_count} (1 parent + 2 vintages), got {len(n.storage_units)}"
        )
    else:
        print(f"  [OK]StorageUnits expanded: 1 parent + 2 vintages = {len(n.storage_units)}")

    # ── Restore path ────────────────────────────────────────────────
    # Walk undo_actions in reverse, applying each "col" entry back to
    # the network. Confirms the parent extendable / bounds get reset
    # exactly to their pre-apply values (no permanent mutation).
    for action in reversed(undo_actions):
        if action[0] == "col":
            _, attr, col, idx, original = action
            df = getattr(n, attr)
            valid = [i for i in idx if i in df.index]
            if valid:
                df.loc[valid, col] = original.loc[valid]

    print("\n" + "=" * 80)
    print(" RESTORE-PATH ASSERTIONS")
    print("=" * 80)

    # After restore: parent line should be back to NOT extendable, s_nom_max back
    # to default (inf or whatever it was before — defaults to inf for Line in PyPSA).
    if bool(n.lines.at["Bus1-Bus2", "s_nom_extendable"]):
        failures.append("Line 'Bus1-Bus2' should be non-extendable after restore")
    else:
        print("  [OK] Line 'Bus1-Bus2' restored to non-extendable")
    if bool(n.transformers.at["Bus2-Bus3", "s_nom_extendable"]):
        failures.append("Transformer 'Bus2-Bus3' should be non-extendable after restore")
    else:
        print("  [OK] Transformer 'Bus2-Bus3' restored to non-extendable")

    print("\n" + "=" * 80)
    if failures:
        print(f" [X] {len(failures)} FAILURE(S):")
        for f in failures:
            print(f"   [FAIL]{f}")
        sys.exit(1)
    else:
        print(" [PASS] ALL ASSERTIONS PASSED (apply + restore)")
    print("=" * 80)


if __name__ == "__main__":
    run_qa()
