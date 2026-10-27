# PR #726 evidence: real app

Shots of the real React app (Vite dev server) against the native FastAPI backend, signed in as the admin account. No network mocks, no store injection except the public navigation actions (`openFlowTargetInPlace`, `showGridReadiness`).

Data: `build.py` builds four sites with `tests/test_grid_readiness_unbalance._site` (one process unit, 20 MVA rated):

| project | single_phase_share | S_k'' | expected row |
|---|---|---|---|
| `unb-pass` | 0.5 (50 %) | 1400 MVA | pass, u2 0.79 % |
| `unb-warn` | 1.0 | 1000 MVA | warn, u2 2.2 % |
| `unb-empty` | empty | 1400 MVA | unknown, "Go to field" |
| `unb-balanced` | 0 | 1400 MVA | pass, u2 0 % |
| `unb-partial` | 0.5, plus a 5 MVA `pump` with none | 1400 MVA | unknown, "At least 0.79 %" |

Each is imported through `/api/io/import/netcdf`, saved, and given a published grid-code copy of `eu_rfg_dcc_ce` with `unbalance_limit_pct` 2.0 % (a demo value; no shipped profile sets it yet). The scorecard reads the campus study's last-run profile, so `build.py` writes `campus_electrical/settings.json` with `eu_unbalance` into each project, standing in for a study run.

`capture.mjs` takes, in Guided and Expert:
1. the Link card's Single-phase share field, empty (`1a`) and filled with 50 (`1b`), next to #727's flicker fields in the Performance group;
2. the `voltage_unbalance` scorecard row, expanded, in the four states;
3. Expert only: "Go to field" from the unknown row, landing on the compressor's card with the field in view. Guided prints "Editable in Expert: ..." on the row instead of the button, as for the harmonic and flicker rows.

**Re-shoot @cb94379d (UX NOT OK @2bcf3105 fixes)** (`ONLY=warn,empty,partial node capture.mjs`): the `warn`, `empty` and new `partial` rows in Guided and Expert, with the unit "% of positive sequence". An unknown row now prints "Project: not known" and no sum; the partial row says "At least 0.79 % from the units with inputs". The note is in plain words. The other shots are from the earlier head.

Built from the local merge of `claude/dc12-unbalance` (c9884ce) with master and #727's head 4224de1.
