# 02c — A weak-grid study from chat or the route runs on the network's connection limit

Status: ready-for-agent

Raised by GK B (64) on #1626 (ask 5, C6). Follow-up to 02a (#1626).

## Problem

#1626 makes every Guided and run path take a weak-grid study's `import_p_nom_mw`
from the network (`flow_service.import_capacity`) and refuse where the network fixes
no limit. Two paths still send no cap and fall back to the pack's constant:

- `run_eh_study` from chat without `pack_overrides.import_p_nom_mw`
  (`harness/catalogue.py:1294` documents "default 50 MW");
- `POST /api/results/eh_study` with no import cap.

Both reach `models/energy_hub.py:372`, `ImportOverlaySpec(import_p_nom_mw=50.0)` in the
`weak_flexible` pack. The study then runs a 50 MW grid connection the network may not
have. That breaks GK B (63)'s standing rule: a network-derived study input never falls
back to a template constant.

## Wanted

- A weak-grid study request with no `import_p_nom_mw` takes the network's limit
  (`import_capacity` on the study's private copy, under the study lock). Where the
  network fixes none, it refuses with the same plain sentence. An explicit cap that
  differs from the network's is refused with both values (as `run_eh.freeze` does).
- The catalogue text no longer says "default 50 MW".
- Tests: the chat tool and the route without a cap (network 45 MW → 45), with 0 MW or
  no link (refusal), and with a different explicit cap (refusal).

## Notes

The pack default is engine code (`models/energy_hub.py`, `eh_study_runner`), so the plan
goes to GK A as well as GK B.

## Comments

- 2026-11-17 GK A 30 plan GO with P1–P7 (as relayed in the builder's brief):
  - P1: fill under the lock before the pack is built; the test asserts the worker's overlay = 45 (network 45 MW).
    Met: `check_eh_study_start` fills it under the lock and rebuilds the pack; route and chat tests.
  - P2: red without the fill. Met: with the fill replaced by `pass`, 16/25 tests fail (the worker got 50.0).
  - P3: exact compare; the sentence names both values and "change it on the network".
    Met: 45.000001 against 45 is refused.
  - P4: NaN / inf / 0 / no-link refusal tests. Met: route and chat, each with #1626's sentence.
  - P5: `import_capacity` takes no PyPSAService lock, stated or pinned.
    Met: `test_import_capacity_takes_no_network_lock`, plus a comment at the call site.
  - P6: the study cache/digest keyed on `pack_overrides` sees the filled value.
    Met: the record's `pack_overrides` and `pack_hash` carry it; the readiness cache is keyed on the network revision.
  - P7: mutants (drop fill, drop refusal, `!=`→`<`, all archetypes, fill even when explicit).
    Met: 18/18 generated, plus the hand mutants, killed; one equivalent survivor (explicit wins over the network).
- 2026-11-20 GK A 32 NOT OK @e4a46332 (#1772), D1: the cap was read under the start lock but the
  worker copied the network after that lock was released, so an edit in between ran a stale cap
  that was not marked edited.
  Fixed: the worker reads the cap again under the lock it captures the revision under, and fails
  the study naming both values. Two lock-entry tests cover it (route and chat).
- Follow-up filed: `02d-readiness-preview-import-cap.md` (`GET /eh_readiness` without a cap still previews 50 MW).
- 2026-11-20 GK B 68 NOT OK @e4a46332 (#1772): (1) the exact float compare refused a matching cap
  (links 10.7 + 12.6 MW sum to 23.299999999999997). Fixed: `_same_mw` (`math.isclose`, rel 1e-9) at
  the start check and at worker entry; two-link tests by route and chat. (2) The P6 cache finding and
  (3) the mutant counts are now stated in the PR body.
