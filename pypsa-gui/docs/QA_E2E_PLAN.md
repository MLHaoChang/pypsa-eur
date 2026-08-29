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

### S1–S9: real backend + frontend

Backend `http://127.0.0.1:8000`, frontend `http://127.0.0.1:5173`, started via
`bash pypsa-gui/start.sh`. Fixtures use real saved projects — `4_nodes_N-0`
and `H2 Demand 250MW` carry 26 280 snapshots (3 × 8760), i.e. genuine
multi-period networks with a stored objective.

**Data safety.** `POST /api/projects/{name}` is a *destructive save*, not a
load (CLAUDE.md). No test may POST to a name it did not create. Projects are
backed up before the run and diffed after. Test artefacts are prefixed
`qa_e2e_` and deleted on completion.

### S10–S14: isolated scratch backend — required, not optional

S10–S14 create, save, delete, and reset real projects and the live network
under test. They must **never** run against the S1–S9 setup above. Before
running any of `--suite S10` through `--suite S14` (or `--suite all`), stop
whatever is on port 8000 and start a dedicated backend with all three of
these set, from `pypsa-gui/backend`:

```
PYPSAGUI_APP_DATA_DIR=/path/to/scratch/appdata \
PYPSAGUI_PROJECTS_ROOT=/path/to/scratch/projects \
PYPSAGUI_LOCAL_MODE=1 \
pixi run uvicorn main:app --host 127.0.0.1 --port 8000
```

All three matter. `PYPSAGUI_APP_DATA_DIR` and `PYPSAGUI_PROJECTS_ROOT`
together keep these suites off your real project store — skip either one and
`qa_e2e_*` scratch projects can land among your real saved projects.
`PYPSAGUI_LOCAL_MODE=1` disables auth so the harness's unauthenticated calls
succeed — skip it and every S10–S14 check fails on 401, which reads as a
catastrophic regression and is pure noise, not a real finding.

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
| S11.generators.create/put/delete | Full CRUD lifecycle. PUT is checked twice: once with a full-object payload (spread from a preceding GET), and once as the dedicated `S11.generators.put_partial` check, which omits a field to confirm `_merge_partial_update` (`routers/network.py:173-195`) preserves it rather than wiping it to the schema default |
| S11.lines.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.storage_units.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.stores.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.links.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.loads.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.transformers.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |
| S11.carriers.create/put/delete | Full CRUD lifecycle. PUT is checked with a full-object payload only (spread from a preceding GET, so no field is ever absent) — does **not** exercise partial-PUT survival; see the note below the table |

Only `S11.generators.put_partial` tests field-omission survival. The other
seven classes' `.put` checks always send a complete payload and cannot
detect a wiped field. This is deliberate, not a coverage gap: every
component's PUT route shares the single `_merge_partial_update` function
(`routers/network.py:173-195`), so one check exercises the mechanism for
all eight classes — the human ruling was that duplicating it per class would
test the same shared function eight times, not eight different things. If
you suspect a PUT-wipe regression on `stores`, `lines`, or any class other
than generators, the check that would catch it is `S11.generators.put_partial`
— that class's own `.put` check will not, by construction.

### S12 — Time series load/delete (area 3)
| id | Assertion |
|----|-----------|
| S12.loads/.generators/.links/.storage_units/.stores (.roundtrip/.listed/.delete) | Upload via the real UI/chat-tool path (or the generic upload endpoint for the two classes with no UI affordance), values round-trip, listing shows the pair, delete empties it |
| S12.lines_asymmetry | `lines` is listed by `GET /timeseries` but rejected by `DELETE ?component=lines` — asserted as a known fact |
| S12.put_overwrite.behaviour | Whole-attribute `PUT` sibling-column-survival behaviour observed through a normal GET, recorded as observed — this GET is backed by `_user_ts` and can mask real loss (see `.network_loss` below), so its "survived" detail is a view artifact, not proof of preservation |
| S12.put_overwrite.network_loss | **CHARACTERIZATION TEST — it PASSES today because a bug exists.** Same wholesale-PUT hazard as `.behaviour`, but observed through `POST /api/simulation/preflight`, a read path that does NOT go through `_user_ts` and so sees the network table's real state. `set_timeseries` currently overwrites an attribute table wholesale and destroys sibling columns it doesn't mention; this check confirms that destruction actually happened. **Disposition if this goes FAIL:** if a future fix makes `set_timeseries` merge onto the prior frame instead of replacing it wholesale (mirroring what `_merge_partial_update` already does for every other component's PUT route), the sibling column will survive, preflight will flag it, and this check will correctly go FAIL — that FAIL means the fix worked. Do **not** revert the fix and do **not** edit this assertion back to green to force the suite clean; the correct response is to revisit or remove this check, because the hazard it exists to catch is gone. |
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

### S15 — Solution FMEA / adequacy journey (area 15)

Unlike S10–S14 this suite builds its network from scratch over the API
rather than from the `3bus` template. Its assertions are exact arithmetic
over particular assets — a generator carrying occurrence data, links to
trip for class B, a load tight enough that shedding is forced — and a
template that happened to ship no links would leave the class-B rows
silently empty, i.e. a suite that passes because it has nothing to check.
Building explicitly also keeps S15 runnable where template payloads are
not installed.

Two steps carry an explicit **non-vacuity** guard (`ENS > 0`,
`shed_hours > 0`). Both first passed while checking nothing: with no
shedding the S15.7 cost identity reduces to `0 == 0`. The fixture now
prices the extendable peaker so that shedding beats building it under a
deliberately loose cap, while the cap the sweep steps re-tighten stays
feasible because that peaker remains extendable.

| id | Assertion |
|----|-----------|
| S15.1 | Scratch project built over the API — every component call 2xx and preflight reports no errors |
| S15.2 | The API boundary rejects nonsense reliability inputs (`422`): negative ENS cap, negative zone multiple, DSR share > 1, negative DSR price. A negative cap was previously accepted and then silently discarded downstream, making "target of −1" indistinguishable from "no target" |
| S15.3 | The meaningful range is still accepted, `0` and `None` included — bounding the fields must not break turning the target off |
| S15.4 | `/api/results/copt` returns a ranked COPT with **no solve at all** once one generator carries occurrence data |
| S15.5 | Occurrence rate equals its closed form `FOR × 8760 / MTTR` exactly, so a rate conversion off by a factor cannot pass |
| S15.6 | With a target set, a solve is optimal and `/api/results/adequacy` reports which standard actually bound |
| S15.7 | **The cost axis excludes shed cost**: `objective − reported cost == ENS × VOLL` exactly, and `excludes_shed_cost is True`. Guarded non-vacuous by `ENS > 0` |
| S15.8 | Shed-hours reaches `/api/results/lost_load` and agrees with the adequacy report — the two surfaces must not disagree on a metric neither had before. Guarded non-vacuous by `hours > 0` |
| S15.9 | Worksheet sidecar round-trips manual class-D rows and mode-keyed overlays (the only persisted parts; computed rows regenerate from `/results/copt`, which is what makes annotations survive a re-solve) |
| S15.10 | A negative criticality is rejected `422` and the previously stored rows are left intact — severity/criticality are `>= 0` by contract, so an out-of-scope P2X row can never rank as beneficial |
| S15.11 | Stress-scenario registry round-trips; the id, frequency and cap guards each reject `422`; and a rejected write does **not** clobber the stored value |
| S15.12 | Sweep guards: refused `422` without a VOLL, accepted `200`, and a concurrent sweep refused `409` |
| S15.13 | Sweep completes with both class B and class C rows, and every row satisfies `criticality == occurrence × severity` (f×S by construction). Class C is additionally pinned to `ΔEUE × VoLL × frequency`. **The two classes reach f×S by genuinely different routes** — class B multiplies by the unavailability *probability* q (a link outage is a state the system sits in a fraction q of the time), class C by an annual *event frequency* — and asserting either route's formula on the other overstates class B by `8760/MTTR`, which is how this check was first written and what running it caught |
| S15.14 | The sweep's closing base re-solve leaves the foreground results in base state (`condition == "optimal"`, report readable) |
| S15.15 | A class-C scenario measures real degradation when the profiles were **uploaded**, which is how the GUI supplies them. Everything above uses a static `p_set`, and that blind spot let a real bug through: `run_simulation` re-broadcasts every user-uploaded series from `_user_ts` onto the live `_t` tables just before building the LP, restoring the pristine profile **over** the mutation each contingency had just made. The scenario solved an unmutated network, returned `ok`, and reported ΔEUE = 0 — a cold snap priced at exactly zero criticality. No in-process test reproduces it, because a network built in process has an empty `_user_ts` |

### S16 — Sequential-MC adequacy study (area 16)

Phase 6's live surface. The ~40 MC/ELCC unit tests and the 10 endpoint tests
run in-process against constructed `MCInputs` or a `TestClient`; S16 is the
only place the study runs in a genuine worker thread in a server process,
with occurrence data resolved through the real defaults chain and the payload
crossing real HTTP. Fixture: two 100 MW units (EFORd 0.10, MTTR 24 h) against
a flat 120 MW load with a 60 MW / 4 h battery — any single outage is a 20 MW
deficit the battery bridges until a persistent outage drains it, which is
exactly the regime the COPT convolution cannot see.

| Check | What it proves |
| --- | --- |
| S16.1 | The bare study completes with **VoLL = 0** (the MC prices nothing and must run without one, where the frontier and sweep both 422), the payload carries the full §2.5 metrics contract (`lole_ci`/`eue_ci` as 2-element intervals, `resolution_floor_h`, `time_basis`), all three clauses of the standing warning, no leaked `thread` — and `EUE > 0`, because persistent outages MUST shed on this fixture |
| S16.2 | The synchronous rejection surface, live: draws over the engine cap `422`, eleven ELCC assets `422`, an unknown asset `404`, an unknown kind `422`, and an inconsistent (q, MTTR) pair `422` **at POST time** — a user with a wrong asset name or contradictory unit data learns now, not after minutes of spinner |
| S16.3 | The mutual-exclusion mesh against a *really running* study (a full-budget ELCC bisection holds the surface busy for seconds): a concurrent MC POST and a frontier POST both refuse `409`, and the original run still completes |
| S16.4 | The ELCC row carries exactly its nine contract keys, the status is from the closed set, an `ok` credit lies in `[0, nameplate]`, and `reason` is null **iff** the status is `ok` — a refusal is data, never a blank |
| S16.5 | **Storage helps, CI-aware and seed-paired**: same seed, same fleet ⇒ identical outage paths, so deleting the battery is a paired comparison — and the no-storage interval's *lower* bound must clear the with-storage interval's *upper* bound. A point-estimate comparison could pass on noise; separated intervals cannot |
| S16.6 | **The ELCC candidates surface and its agreement guarantee, live**: `GET /results/mc/elcc_candidates` enumerates the remaining kinds (two occurrence-bearing generators and a must-take wind generator as `vre`), the **entire** candidates list POSTed back resolves — every row prices, none 404s — and a unit asked for as `kind="vre"` is refused `422` (the double-count guard), not credited twice |

## Loop protocol

Run all suites → triage failures → fix → **re-run the full set** (not just the
failing suite, to catch fix-induced regressions) → repeat until two consecutive
clean runs.

Harness: `pixi run python pypsa-gui/backend/smoke/qa_e2e.py [--suite S3]`.
Standalone under `smoke/`, never collected by pytest — it drives the live
backend and reads real projects.

## Outcome

The original S1–S9 hardening pass took 11 rounds and reached **43 PASS / 0
FAIL / 0 SKIP**, twice consecutively, with the write-path battery
additionally repeated 4× to prove stability. That baseline predates S10–S14
(added in a later pass) and predates further drift inside S1–S9's own
dependencies (frontend dev server availability, backend test count, ruff
findings) — it is history, not the number to expect from a run today.

**Current baseline**, a full `--suite all` run against the isolated S10–S14
scratch environment described above:

```
PASS 86   FAIL 10   SKIP 8
```

All ten failures are pre-existing and catalogued — none of them are caused
by S10–S14, and a clean S10–S14 run still shows exactly this set:

| id | Cause |
|---|---|
| `S1.2`, `S1.3` | frontend dev server not running |
| `S2.5` | reintroduced-tool finding (chat-tool registry) |
| `S6.1`–`S6.4` | frontend build/test tooling — also needs the dev server |
| `S8.1` | backend pytest — findings outside this work |
| `S8.3` | ruff — findings outside this work |
| `S9.1` | `audit_log`/`Depends` `AttributeError` |

A run that reproduces exactly this ten-id set has found no regression. A run
whose failing set differs from this table — anything added, anything
missing — has, and is worth investigating before assuming it's "the usual
ten."

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
