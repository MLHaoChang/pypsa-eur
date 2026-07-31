# End-to-end QA plan — post-migration hardening

Covers the six changes landed after the Windows → macOS port:

| # | Change | Commit |
|---|---|---|
| 1 | Removed 8 unimplemented chat-tool registrations | `323b9f0b` |
| 2 | `npm audit fix` (7 → 2 advisories) | `887e6b60` |
| 3 | ruff autofixes across the backend | `2f682f46` |
| 4 | Period-weighting basis routed through `period_utils` | `e6fbc385` |
| 5 | Shared `services/economics.py`; results↔compare untangled | `67aa9156` |
| 6 | Vitest + pure-helper coverage | `796f0b28` |

Items 4 and 5 changed **numeric** code paths that had no pre-existing test
coverage. They are the reason this plan exists: the unit suites pass, but
they never exercised multi-period weighting or the CO₂ string-coercion path
against a real solved network.

## Environment

Backend `http://127.0.0.1:8000`, frontend `http://127.0.0.1:5173`, started via
`bash pypsa-gui/start.sh`. Fixtures use real saved projects — `4_nodes_N-0`
and `H2 Demand 250MW` carry 26 280 snapshots (3 × 8760), i.e. genuine
multi-period networks with a stored objective.

**Data safety.** `POST /api/projects/{name}` is a *destructive save*, not a
load (CLAUDE.md). No test may POST to a name it did not create. Projects are
backed up before the run and diffed after. Test artefacts are prefixed
`qa_e2e_` and deleted on completion.

## Suites

### S1 — Service & contract health
| id | Assertion |
|----|-----------|
| S1.1 | Backend serves `/docs`; OpenAPI parses; route count > 150 |
| S1.2 | Frontend serves on **IPv4** `127.0.0.1:5173` (the macOS `::1` regression) |
| S1.3 | Vite proxy forwards `/api/*` to the backend |
| S1.4 | Every `TOOL_ROUTES` HTTP path resolves against the live OpenAPI schema |

### S2 — Chat tool surface (guards change 1)
| id | Assertion |
|----|-----------|
| S2.1 | `len(TOOLS) == len(DISPATCHERS)` — the invariant the deleted registrations broke |
| S2.2 | Every schema name resolves to a callable dispatcher |
| S2.3 | No dispatcher is missing a `TOOL_ROUTES` entry |
| S2.4 | `/api/chat/health` reports ok |
| S2.5 | The 8 removed names are absent from **both** registry and schema |
| S2.6 | Every tool's schema `required` array is satisfiable by its Python signature |

### S3 — Results endpoints on a real solved network (guards changes 4, 5)
| id | Assertion |
|----|-----------|
| S3.1 | Every `/api/results/*` endpoint returns 200 or a clean 204/409 — never a bare 500 |
| S3.2 | Responses are valid JSON (no NaN/Inf reaching `JSONResponse`) |
| S3.3 | `cost_breakdown` totals are finite and non-negative |
| S3.4 | Multi-period: Σ per-period values reconciles with the horizon total |
| S3.5 | `emissions` uses the shared CO₂ map and stays finite |

### S4 — Numeric equivalence vs pre-refactor (the core risk)
| id | Assertion |
|----|-----------|
| S4.1 | `snapshot_weights` matches the pre-refactor implementation on every real project |
| S4.2 | Subset weighting ≠ full-horizon weighting (proves the `sns` parameter is load-bearing) |
| S4.3 | `co2_intensity_map` matches compare.py's original on every real project |
| S4.4 | Results-tab and Compare-rail emissions agree for the same network |

### S5 — Compare view (guards change 5)
| id | Assertion |
|----|-----------|
| S5.1 | `compare-state` returns 200 for each project |
| S5.2 | Hoisted module-level imports did not break `lp_scaled_load_frame` / `corrected_marginal_prices` |
| S5.3 | Economics comparison is finite and JSON-clean |

### S6 — Frontend (guards changes 2, 6)
| id | Assertion |
|----|-----------|
| S6.1 | `npm test` — 37/37 pass |
| S6.2 | `tsc --noEmit` clean |
| S6.3 | Production build succeeds |
| S6.4 | Served HTML references a bundle that actually loads (no 404 on the JS asset) |
| S6.5 | `coerceForColumn` extraction did not break BottomPanel's import graph |

### S7 — Write-path smoke (data-mutating; isolated project)
| id | Assertion |
|----|-----------|
| S7.1 | Create `qa_e2e_*` project from template; it appears in the list |
| S7.2 | Component CRUD round-trips without wiping unspecified fields |
| S7.3 | Undo restores prior state |
| S7.4 | Delete removes it; the 12 pre-existing projects are byte-identical |

### S8 — Regression suites
| id | Assertion |
|----|-----------|
| S8.1 | 1006 backend tests pass |
| S8.2 | 26 PyPSA-Eur unit tests pass |
| S8.3 | ruff finds only the 7 known-benign findings |

### S10 — Project save/load round trip (area 1)
| id | Assertion |
|----|-----------|
| S10.2 | Full-object `GET`-then-`PUT` on a bus sets a distinctive `v_nom` and preserves the untouched `carrier` field |
| S10.3 | `POST /api/projects/{name}` (destructive save) succeeds |
| S10.4 | `GET /api/projects/{name}` (load) followed by `GET /api/network/buses` shows the saved `v_nom` — the literal save/load round-trip content check |
| S10.5 | After `POST /api/network/reset` + re-activate, the round-tripped `v_nom` is still served (guards resident-state papering over a disk-read bug) |

### S11 — Asset CRUD across component classes (area 2)
| id | Assertion |
|----|-----------|
| S11.buses.delete | Individual bus `DELETE` removes it (create/GET/PUT/undo already covered by S7) |
| S11.generators.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.lines.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.storage_units.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.stores.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.links.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.loads.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.transformers.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |
| S11.carriers.create/put/delete | Full CRUD lifecycle with partial-PUT-survival check |

### S12 — Time series load/delete (area 3)
| id | Assertion |
|----|-----------|
| S12.loads/.generators/.links/.storage_units/.stores (.roundtrip/.listed/.delete) | Upload via the real UI/chat-tool path (or the generic upload endpoint for the two classes with no UI affordance), values round-trip, listing shows the pair, delete empties it |
| S12.lines_asymmetry | `lines` is listed by `GET /timeseries` but rejected by `DELETE ?component=lines` — asserted as a known fact |
| S12.put_overwrite | Whole-attribute `PUT` sibling-column-survival behaviour, recorded as observed |
| S12.snapshot_mismatch | Zero-overlap upload's effect on `ts_start`/`ts_end`, recorded as observed |

### S13 — Fresh solve + result validation (area 5)
| id | Assertion |
|----|-----------|
| S13.1 | Pre-check `GET /api/simulation/status` before touching anything else (Hazard 5) |
| S13.2 | `POST /api/network/reset`, strictly after S13.1 |
| S13.3 | Fresh `qa_e2e_solve` project created and activated |
| S13.4 | `POST /api/simulation/run` accepted |
| S13.5 | Solve completes within the poll ceiling |
| S13.6 | `status == "completed"` and `objective` is finite |
| S13.7 | `RESULT_ENDPOINTS` walk against the fresh solve — no 5xx, no non-finite values |
| S13.8 | Re-solve reproduces the first objective within `1e-6` relative tolerance |
| S13.9 | Every `RESULT_ENDPOINTS` entry names a route that actually exists as `/api/results/{name}` in the live `/openapi.json`. Added because a dead entry is invisible to S13.7/S3.1/S3.2: a missing `/api/*` path falls through to the SPA catch-all and returns 404, which is `<500`, valid JSON, and float-free — it trips none of those checks' tripwires. FAIL means either `RESULT_ENDPOINTS` names a route that was removed/renamed, or a route it expects was never added — fix whichever is stale. |

### S14 — Scenario tree & snapshots (area 7)
| id | Assertion |
|----|-----------|
| S14.2 | Snapshot creation |
| S14.3 | Snapshot listing shows the new snapshot |
| S14.4 | Diverge the base project from its snapshot (create a bus) — setup so S14.7's restore has a real mutation to roll back |
| S14.5 | Scenario branch creation (`201`) |
| S14.6 | Scenario tree listing via `parent_project` filtering shows the branch |
| S14.7 | Snapshot restore rolls back a post-snapshot mutation |
| S14.8 | Snapshot deletion |
| S14.9 | Delete without `cascade` is blocked (`409 descendants_exist`) |
| S14.10 | Cascading delete removes both base and branch |

## Loop protocol

Run all suites → triage failures → fix → **re-run the full set** (not just the
failing suite, to catch fix-induced regressions) → repeat until two consecutive
clean runs.

Harness: `pixi run python pypsa-gui/backend/smoke/qa_e2e.py [--suite S3]`.
Standalone under `smoke/`, never collected by pytest — it drives the live
backend and reads real projects.

## Outcome

11 rounds. Final state **43 PASS / 0 FAIL / 0 SKIP**, twice consecutively,
with the write-path battery additionally repeated 4× to prove stability.

### Product bug found and fixed

**Deleting a project did not evict its resident in-memory context.**
`delete_project` removed the directory but left the `ProjectContext` in the
registry, and `activate_project` has a resident fast path — a pure pointer swap
with no disk I/O. So *delete → recreate the same name from a template →
activate* served the **deleted** project's network. A brand-new project came up
carrying ghost components that were never in its template, and the next save
persisted them to disk.

Reproduced from a never-used name: create from `3bus` (Bus 0/1/2) → add
`qa_bus` → delete → recreate → activate → **four** buses including `qa_bus`.

Fix: `delete_project` now calls `PyPSAService.drop()` for every successfully
deleted name. Guarded by `test_delete_project_evicts_resident_context` and
`test_delete_project_eviction_is_safe_for_never_resident_names`
(test_registry.py) — the first was verified to FAIL with the fix disabled — and
by S7.5 end-to-end. The harness deliberately does **not** call
`/api/network/reset` first, so re-running S7 genuinely re-exercises this path.

### Test-hygiene bug found and fixed

**30 chat tests wrote into the real projects directory.** They called
`run_turn`, which persists a turn to `chat.jsonl` under the active project,
without requesting the `tmp_projects_dir` fixture (deliberately not autouse).
Each full-suite run appended stub turns to
`projects/scen1/chat.jsonl` — 8 lines accumulated across 4 QA rounds before it
was caught by the post-run tree diff. All 30 now take the fixture; verified the
file's md5 is unchanged across a full suite run.

### Harness defects fixed during the loop

Rounds 1–2 found only harness bugs, which is itself a finding — the first pass
was not adversarial enough, prompting suites S9 (edge cases), S6/S8
(frontend + regression), and the `results-summary` coverage gap.

| Defect | Cause |
|---|---|
| `InvalidURL` on 3 projects | project names contain spaces; no percent-encoding |
| `openapi paths > 150` | conflated paths (141) with operations (178); now asserts core routes exist |
| S7 create 404 | wrong endpoint — real one is `POST /from_template/{id}?name=` |
| S7.2 misleading diagnostic | `st` reused for the follow-up GET, so it reported the GET's code as the create's |
| S7.3 flaky | accepted a bag of status codes; now asserts against actual undo depth |
| S4.4 vacuous | asserted "both surfaces reachable" instead of comparing numbers |

### Notable confirmations

* `/api/results/emissions` **561335.176 tCO₂** ≡ `results-summary` **561.335 kt**
  — the Results tab and Compare rail agree, which is what the shared
  `co2_intensity_map` exists to guarantee.
* Σ per-period cost **488727012.3416** ≡ horizon total, on a real 3-period
  26 280-snapshot network.
* `snapshot_weights` bit-identical to the pre-refactor implementation across
  all 10–11 real project networks; subset ≠ horizon on 5–6 of them, proving the
  `sns` parameter is load-bearing.
* 4 real networks still resolve CO₂ intensities when the column is re-typed to
  strings — the exact case that previously reported zero emissions.
* `is_multi_period` JSON key preserved after the local-variable rename.
* 24 concurrent `/results/*` reads, zero 5xx.
* **Zero user-data drift** across all 11 rounds (byte-identical tree diff).

### Known-benign, left alone

7 ruff findings, all in test files: 3 `F841` unused locals, 2 `D301`
escape-in-docstring, 1 `UP028`, and 1 `F821` for a quoted forward-ref
(`ProjectContext`) that is never evaluated.

### Observation, not fixed

`POST /api/projects/{name}` is a destructive save that matches **any**
unmatched path segment, so a mistyped endpoint silently becomes a project — a
round-1 typo created one called `create_from_template`. It overwrote nothing
here (no project had that name) and was removed, but the same typo against an
existing name would overwrite it. Reserving route-like names, or requiring an
explicit create flag, would close this. Out of scope for this pass.
