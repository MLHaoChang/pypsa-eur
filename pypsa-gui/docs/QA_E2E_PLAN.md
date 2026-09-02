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

### S17 — The adequacy-coupled planning loop (area 17)

Phase 7's live surface. The controller has 22 unit tests against fake
callables and the route has 26 through a `TestClient`, but neither drives what
this study *is*: real HiGHS capacity expansions, re-solved under a retuned
cap, evaluated by the real sampler on whatever plan the LP actually produced,
in a worker thread in a server process. The mesh fixes in particular are HTTP
facts, not unit-test facts.

**Non-vacuity is self-calibrated.** The suite first runs a plain MC study to
learn the fixture's own LOLE, then targets a *third* of it — so iterate 0 is
guaranteed to miss and the loop cannot pass by doing nothing. A hardcoded
target risks a fixture that meets immediately and a suite that proves nothing.

| Check | What it proves |
| --- | --- |
| S17.1 | The whole synchronous rejection surface, live: no VoLL, no target, zero target, draws over the engine cap, budget over `MAX_LOOP_SOLVES`, an invalid `restore`, a target below the resolution floor, and the `myopic`/`rolling` strategy guard — all `422` **before** a solve is spent. The strategy guard is the one that matters most: without it every capped iterate fails validation and the loop reports "unreachable", a statement about the network, when the truth is a statement about the solve strategy |
| S17.2 | Calibration: the baseline plan solves optimal and its measured MC-LOLE is > 0, so the target (a third of it) is one iterate 0 must miss |
| S17.3 | The loop runs to a verdict **and the mesh holds while it runs**: a foreground solve, an MC study and a second loop are all refused `409` *during* the run — the Phase-7 hole fix, provable only here, because a solve interleaving between iterates rewrites the very `p_nom_opt` the next evaluation reads. Payload contract checked (`study` key, no top-level engine, verdict sentence, `resolution_floor_h`, warning clauses, `base_restored`, no leaked thread), every iterate row carries its full key set, and a `met` verdict must be **verified** — the final iterate's own evaluation, never an extrapolation between steps. And an `unreachable` verdict that names the never-bound mechanism must also name the WAY OUT by the heading of the panel the user has to click — until Phase 9 it named the lever ("a planning reserve margin") without naming the study that now searches for it |
| S17.4 | Abort: a study whose wall-clock promise is "minutes to tens of minutes" must be cancellable, and the closing restore must still run so the network is not left on a swept cap. (The abort is posted *immediately*, not after a sleep: the record is published under the same lock hold that starts the thread, and this loop is fast enough that any sleep long enough to "let it get going" is long enough to let it finish — a first attempt aborted a study that had already terminated) |
| S17.5 | `restore="final"` leaves the user **holding** the certified plan (`ens_cap_permyriad == ε*` read back from the config), and on a non-met verdict falls back to base rather than applying a cap no verdict certified |
| S17.6 | the verdict names the SAME number the panel's restore explainer tells the user to type — one certified cap, one spelling. **SKIPs on this fixture**, and says so: S17's network is the one where the cap is unreachable by construction, so no run here certifies a cap and there is no number to check. Recording a PASS would read as live coverage this suite cannot provide; the bitten unit tests and S19.6 carry it |

**What the first live run found.** The fixture returns `unreachable`, and
correctly: `ens_mwh = 0` and `binding = "voll"` at *every* cap, because 200 MW
of firm capacity covers a 150 MW load and the LP models no outages — so the
LP sheds nothing at any ceiling, no cap can change the plan, and the MC's 10.9
hours of loss of load come entirely from outages the proxy never sees. The
loop reached that answer in **two solves** (the informed jump crossed the
whole slack region in one step), which is the search discipline working. But
the verdict copy named the three mechanisms the design had anticipated —
storage foresight, DSR, storage-for-thermal substitution — and **none of them
was what happened**, sending the reader after causes that were not there. The
never-bound case is diagnosable from the rows (`binding` on every solved
iterate), so it is now diagnosed by name, with the honest next action: an
energy cap has no leverage on outage-driven risk; firm-capacity headroom does.
S17.3 pins the copy live.

### S18 — The firm-capacity reserve margin (area 18)

Phase 8's live surface. The constraint has 55 unit tests, the
preflight/report/endpoint 31 more, and three self-calibrated acceptance tests
prove the lever moves MC-LOLE (12.41 h → 1.32 h with separated intervals) — but
none of them crosses HTTP. This suite drives what a user touches: the config
field at the API boundary, the preflight refusals that replace an
unimplementable "let the LP go infeasible", and the derating table that makes
the phase's proxies inspectable.

**The margin is derived from the fixture, never chosen** — the same discipline
the Phase-8 review forced onto the acceptance tests, for the same reason: a
value inside the largest-unit step buys real megawatts and moves LOLE not at
all.

| Check | What it proves |
| --- | --- |
| S18.1 | The margin is bounded **at the API boundary** — `-1` and `600 %` refused `422`, `0` / `None` / `0.15` accepted. The Phase-1 QA round found four reliability fields accepted and then silently discarded; a margin that never reaches the solver is indistinguishable from no margin at all |
| S18.2 | An unreachable margin is refused **before the solve**, as an error naming both numbers. This is the check that replaces "let the LP go infeasible", which was never implementable: linopy raises on a constant constraint and `Generator-p_nom` does not exist when nothing extendable is active |
| S18.3 | At the derived `m*` the constraint **binds and builds**: 50 MW of peaker the LP had no economic reason to build, `required == firm == 223.5 MW` against a 150 MW peak, the `horizon_wide` label true (one `p_nom` variable, so one standard at the maximum peak), and **every credited asset carries its `basis` and `source`** — a derating proxy nobody can trace is a number nobody can check |
| S18.4 | `met` and `binding` are different questions: at a margin the fixed fleet already satisfies, the standard is met and **not** binding, and nothing is built. Conflating them would credit the margin for capacity that was always there |
| S18.5 | The margin does not leak into the contingency sweep. Without the strip, `freeze_capacities` pins the peaker and every contingency that removes derated capacity violates the standard — so the sweep dies infeasible and every severity would read as the standard rather than the outage |

**What the first live run found:** nothing in the product — but S18.2 was
initially **vacuous in a way worth recording**. It set a 900 % margin, which the
schema correctly refuses (`le=5`), so the config never took and the preflight
had nothing to complain about while the check reported a clean pass. It now
uses 400 % (unreachable against a 651 MW maximum, inside the bound) **and
asserts the config write returned 200**, so it cannot pass against a margin
that was never set. The lesson generalises: a live check that configures
something must assert the configuration took.

### S19 — The margin loop (area 19)

Phase 9's live surface, and the one suite whose job is a **comparative** claim
rather than a contract: on ONE network with ONE derived target, the cap loop
must report `unreachable` and the margin loop must report `met`. That is the
whole reason Phase 9 exists — Phase 7's loop kept correctly answering "the cap
never bound", and Phase 8 built the lever that moves the metric there.

| Check | What it proves |
| --- | --- |
| S19.1 | The two loops refuse *different* things, and neither copies the other blindly: a margin loop runs on a VoLL-free network (a margin is a constraint, not a price) where the cap loop refuses `422`; `myopic` is allowed for the margin (each window is one period, which is the peak the standard is defined against) while `rolling` is refused |
| S19.2 | Calibration: the incumbent plan's own measured MC-LOLE, targeted at a third, so neither loop can pass by doing nothing |
| S19.3 | **The claim.** Same network, same target: cap loop `unreachable`, margin loop `met` at a certified `m*`, with the final iterate's own MC verifying it |
| S19.4 | The payload contract, and the one thing that must never leak — the controller's internal reciprocal. Every number on the wire is a margin; every `cap_mwh` is `None` (spec §2.2); `m*` lies inside the schema bound the loop must respect |
| S19.5 | `restore="final"` writes the **margin's** config field, never the cap's, and a user's own ENS cap survives the study untouched |
| S19.6 | the verdict names the SAME number the panel's restore explainer tells the user to type — one certified margin, one spelling |

### S20 — Refusing a network swap during a study (area 20)
| id | Assertion |
|----|-----------|
| S20.1 | With **no** study running, `POST /api/network/reset` succeeds — the baseline, and it runs FIRST because it is itself a reset: after the fixture build it would wipe the very fixture the rest of the suite needs |
| S20.2 | With a **verifiably live** MC study, the same route is refused `409`, the refusal **names** the study, and it offers only a remedy that exists — the MC has no `/abort`, so the sentence must say it cannot be aborted rather than pointing at a button that is not there. The check reads the study's own status immediately before the swap and **SKIPs rather than fails** if the study finished first, so it can never report a lost race as a broken guard |
| S20.3 | …and the guard **lifts**: once the study finishes the route succeeds again. A refusal that never releases is an outage, not a guard |


### S21 — Profile plus outage data: the preflight disclosure (area 21)

*Re-scoped by Phase 12c-pre.* 12a warned that the engines discarded the
profile (`outage_shadows_profile`); 12c-pre models it in both engines, so the
preflight issue is now a disclosure of how (`profile_and_outage_modelled`),
emitted for outage data the user typed. A library (carrier-default) rate on a
profiled unit gets no preflight issue; the `/copt` and `/mc` payloads carry
the disclosure instead (S24).
| id | Assertion |
|----|-----------|
| S21.1 | The `profile_and_outage_modelled` disclosure reaches a **live preflight**, names the asset, is a `warning` (the only non-error severity on the wire), and the old `outage_shadows_profile` code is absent — with the profile modelled it would be a false statement |
| S21.2 | It says **how** the unit is modelled (the COPT *mixes* it per hour; outages sampled on the series), and is silent both on a profiled farm with no outage data (must-take, netted as before) and on a thermal unit whose `p_max_pu` is a flat 1.0 — the false positive that would make this noise on every real project |

**Why a live suite for a preflight warning.** The unit tests drive
`_check_outage_params` directly; they cannot tell you whether the warning
survives the route, the issue serialisation and the payload. The first attempt
at this check was itself a false green: the fixture's `PUT
/api/network/timeseries/...` returned **405** because the route takes
`{index, columns, data}` rather than `{values}`, so the asset had no profile
at all and the warning correctly did not fire — a passing-looking run that
proved nothing. The suite now asserts the fixture build, so it cannot pass
without the profile it is about.

**What the first runs found — in the suite, not the code.** Three times, and
each was the harness lying rather than the product: `draws=4000` exceeded the
engine's 2000-draw cap so the study never started (the 422 said so plainly
once the detail was surfaced instead of swallowed); the baseline reset was
ordered *after* the fixture build and wiped it, so every later check failed on
an empty network for reasons unrelated to the guard; and on a two-day horizon
the MC finished before the swap was attempted, so a **passing** guard reported
a failure. The fixture now spans a quarter and the check states whether the
study was actually alive — the difference between a check and a coin toss.


**What S19.6 found on its first run — and the near-miss that nearly hid it.**
The check failed live while passing every unit test: the verdict said
`reserve_margin = 0.6716` where the panel said `0.671600430725`. The cause was
not the code — it was the **server**. A `uvicorn` started for this run had
failed to bind (`address already in use`) behind a `nohup ... &`, and an older
process from a previous session answered every request. Every "live" result in
that run described code from before the fix.

Two lessons, both cheap. **Starting the backend is not the same as answering
on 8000**: after `uvicorn` starts, grep its log for `Application startup
complete` AND for `address already in use` before running a single suite — a
`curl /docs` returning 200 proves only that *some* process is listening. And a
live check that cannot distinguish "passed" from "nothing to check" must
**SKIP** rather than PASS, which is why S17.6 does.


**What the first live run found — a real defect in the code it was testing.**
S19.3 reported `unreachable` from the margin loop too. The cause: the
controller's blind step multiplies the margin ~4× per iterate, so from a small
start it leapt clean over the fleet ceiling; the over-ceiling solve failed
validation, was relabelled `infeasible`, and the nesting logic then concluded
— correctly, given what it had been told — that every stricter margin was
infeasible. The loop reported `unreachable` **having never evaluated the
reachable region at all**: ceiling 271 %, last evaluated margin 18 %, and a
plan meeting the target sitting between them. The route now clamps a request
to the ceiling and evaluates *there*, refusing only once the strictest
reachable margin has itself been tried and missed. After the fix the same run
reports `met` at m\* = 0.672. A unit test that had pinned the old solve count
was updated to the corrected contract, with the reason recorded in the test.

A second finding was the suite's own: S19.1 initially asserted `422` from the
cap loop and got `409`, because the margin loop it had just started was still
running — the 409 mesh working exactly as designed, and the test reaching the
wrong question. Ordering fixed, with the reason in the code.

### S22 — A vintage-expanded plan reports what it built (area 22)
| id | Assertion |
|----|-----------|
| S22.1 | On a two-period network with per-period capacity bounds, a margin the LP **met** by building ~36.8 MW of `wind@2030` (35 MW firm at derate 0.95) is served by `GET /results/reserve_margin` as `met=True` in **both** periods at the firm capacity the plan actually has — not `met=False` at the fixed fleet's 190 MW |
| S22.2 | The **vintage rows** carry their built sizes: `wind@2030` at ~36.8 MW in 2030 *and* in 2040 (a 2030 vintage is active later), and `wind@2040` at `0.0` — built-to-zero, never `null` |

**Why this suite exists.** The Phase 12b (v3) plan claimed that
`reserve_margin_payload` is the one post-solve point with built capacity in
scope. Its review checked the premise on a vintage-expanded network and found
the shipped payload wrong there: the solve expands `wind` into transient
`wind@2030` / `wind@2040` rows, the wrapper stashes those names, and the
restore drops the rows *before* the payload reads capacities, so `_built()`
found nothing and credited zero. The reserve margin had mis-reported the
network class it exists for since it landed. The unit test drives
`run_simulation` directly; this suite drives the fixture, the bounds and the
solve over HTTP and reads the surface a user reads.

**Why the candidate carries outage data when the unit test's does not.**
The unit test's wind is must-take: a time-series profile and no outage data.
Over HTTP that cannot be built — the generator API takes a static `p_max_pu`,
the margin's profile test is a time-series column check, and a per-period
profile cannot be set over the API on a multi-period network (recorded above
under S21). Without either, preflight correctly refuses the unit as
`reserve_margin_unpriceable_assets` — the first run of this suite hit exactly
that and read as `validation_failed`, so the suite now names the preflight
refusal in its detail rather than leaving it opaque. The candidate carries
outage data instead and is a sampled unit at derate 0.95; the thing under
test — the vintage row's built capacity reaching the payload — does not
depend on which membership the unit has. **Bitten live**: with the vintage
lookup removed, both checks fail (`met=False`, firm 190, every vintage row
`None`).

**What is deliberately NOT here.** The companion defect found in the same
review — a margin run that fails between optimize and the report step leaves
its stash on the network, and the *next* solve, one that set no standard,
publishes a margin verdict (or a full adequacy report claiming an energy
target was set and binding) built on the dead run's targets — has no honest
live reproduction: it needs an exception at a point no API input reaches. It
is covered by two unit tests, one per stash, and this plan says so rather
than shipping a check that could only pass.

### S23 — The net-load window, live (area 23)
| id | Assertion |
|----|-----------|
| S23.1 | On a flat-load network with one 100 MW wind farm whose profile is 1,0,1,0, `GET /results/reserve_margin` serves a `net_window` with `status="ok"`, the two hours the wind is **absent** as its snapshots, `netted_mw = 50`, and the farm's `derate_net = 0.0` beside its gross `derate = 0.5` — what the credit would have been on the hours the system actually runs short |
| S23.2 | The same farm with a **flat 1.0** column reads `profile_kind = "constant"`, is not netted, and the block says `nothing_netted` with an empty window — never a zero-delta window dressed as a finding |

**Why single-period.** A per-period profile cannot be set over the API on a
multi-period network (recorded under S21 and S22), and the window is a
per-period object regardless, so the flat network is the honest live surface
for it. The vintage path — the net window on a row the restore has dropped —
is covered by the unit test on the alternating-profile vintage fixture, whose
built size (70 MW) and net derate (0.0) the plan review reproduced end to end.

**Why the copy matters here.** The panel line and the "Net derate" column are
a SECOND PROXY in the margin's own units, never a correction, and "netted
capacity" is not "VRE" — a thermal maintenance schedule is netted too. Both
are pinned by component tests that assert the words do not appear.

### S24 — Profile plus outage data: the engines, live (area 24)
| id | Assertion |
|----|-----------|
| S24.1 | On 12a's two-farm fixture (100 MW flat load; gas1 80 MW q = 0.10; two identical 100 MW farms on the profile 0.05/0.15/0.35/0.45, one with q = 0.10 entered), `GET /results/copt` equals the **mixture computed independently in the suite** from the fixture's numbers alone — `LOLP_h = 0.1·(1 − S(r_h)) + 0.9·(1 − S(r_h − a_h))` over the gas1-only table — **2.78 h**, not the **0.44 h** the flat two-state treatment gave; `fleet.profile_units` names `wind_with_for`, `netted_beyond_cap` is empty, `must_take` counts the other farm from the walk, and `fidelity_note` names the unit and says the COPT *mixes* it |
| S24.2 | A small MC study on the same network finishes and its result's `profile_units` names `wind_with_for` (outages sampled on the series); preflight carries `profile_and_outage_modelled` and not `outage_shadows_profile` |

**Bitten live** (recorded in the plan): with the series dropped at attachment
the COPT reverts to the flat value and S24.1 fails on the number and the
names.

**What is not live here.** A fleet with more than `K_EXACT = 8` profiled
units (the netted remainder) needs nine hand-entered outage rates on nine
profiled farms; the split, the netting and the per-row `note` are pinned by
unit tests on a 4-unit fleet with the cap overridden to 2.

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
