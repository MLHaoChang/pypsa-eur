<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `eh_setup._flag` reads the text "no" as True

Status: done
Type: bug
Found by: GK A review of #1388 (issue 27).

## Problem

`services/adequacy/eh_setup.py` `_flag` used plain `bool()`, so the text "no", "false" or "0" in `eh_poc`, `eh_critical` or `p_nom_extendable` read True. It now uses the one flag rule (`process_chain.truthy`), with the same guarded import and fallback as issue 27.

Acceptance:
- [x] `_flag` equals `process_chain.truthy` on the pinned table (`tests/test_eh_setup_flag.py`).
- [x] "no", "false", "0" read False (red on master before the fix).

## Comments

### 2026-11-11: GK A plan GO, conditions

- C1: the column guard and `except (TypeError, ValueError): return False` stay around the `truthy` call; a multi-element list cell raises in `pd.isna` and reads False, as before. Test added.
- C2: `np.True_`, `np.False_` and `pd.NA` are in the table.
- C3: the revert-to-`bool()` mutant on the guard fails `test_flag_equals_shared_rule['0']` (single test, `-x`).
- C4: readers are `eh_setup.py` `p_nom_extendable` (a link whose flag is the text "no" is no longer treated as sizeable), `already_poc` / `already_crit`, and the proposal "current" fields. No existing test relied on `bool()`.
- C5: same guarded import and `process_uc._fallback_truthy` fallback; `test_packaging_requirements` (bar the container-only defusedxml test), `test_bus_flag_truthy` and the eh_setup suites pass.
