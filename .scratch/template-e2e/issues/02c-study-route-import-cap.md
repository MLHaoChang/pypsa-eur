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
