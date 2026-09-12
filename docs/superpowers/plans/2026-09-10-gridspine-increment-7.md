# gridspine increment 7 — the client's own dispatch as a fourth source (spec stage 1, `producers/external.py`)

**Status:** LANDED on `feat/gridspine-external-producer`, 2026-09-12 — A1, A2, A3 and the vertical slice. Proposed 2026-09-10, after increment 6 landed on `master` in PR #8 (`fb6cb753`). The document was written BEFORE any code because the obvious next items are all blocked on resources this environment does not have, and the choice of what to build instead was the owner's; it is amended below where building it proved the plan wrong.

## Why this increment is a scoping decision, not a task list

Increment 6 closed spec stage 6. What the spec calls "Later" is the natural next bucket, and four things the pipeline still owes are recorded as not-done in increment 6 and in PR #8's description. Every one of them is blocked on something no session here can supply:

| owed | blocked on | can a session here do it? |
|---|---|---|
| ADR 0002 live probe of the twelve `gridspine_*` chat tools | an Anthropic API key | **no** — the tools are structurally tested and unverified against a model |
| a browser run of the study view | a display | **no** — component tests only |
| a real PowerFactory export in `tests/gridspine/fixtures/powerfactory/` | a PowerFactory licence and session | **no** — read-back is exercised against hand-written bundles |
| the macOS `.app` build, hardened runtime, codesign | a Mac | **no** — packaging is proved on Linux only |

None of them is a matter of effort. Building "the next increment" therefore means picking work whose evidence a headless Linux session can actually produce, and saying so rather than producing a plan whose gates cannot be run.

## What the spec still has unbuilt, and which of it is reachable

Reading `docs/superpowers/specs/2026-08-27-gridspine-design.md` against the tree as it stands:

| spec item | state | reachable headless? |
|---|---|---|
| `producers/external.py` — client-supplied snapshots (CSV/Excel) → the same dispatch table | **absent**; named in the package layout since the spec was written | **yes** — pure data work |
| `drivers/connection.py` — variant 2 chain | absent | partly; large, and its shape depends on a real connection study |
| grid-strength & interaction screen (WSCR/ESCR, impedance screen, RoCoF → EMT) | `static/strength.py` has SCR and banding only | yes, but it is connection-study phase 2 and the spec defers it behind a trigger |
| compliance rule engine (VDE-AR-N 411x/412x/413x, RfG) | absent | yes, but the spec defers it behind the same trigger |
| PowerFactory API exporter | absent | **no** — needs PowerFactory |
| PowSyBl / CGMES ingest | absent | blocked by the spec's own MPL policy check |
| clustered producer | deferred | the spec's trigger (a study exceeding nodal-UC capacity) has not fired |

**The recommendation is `producers/external.py`.** It is the one unbuilt item that is named in the spec's package layout rather than in its deferred bucket, is fully testable headlessly, and answers a question the product will be asked immediately: *a client already has their own dispatch, out of their own market model — must they re-solve it in PyPSA to use this?* Today the answer is yes, and the spec says it should be no.

## A — `producers/external.py`: a client dispatch table, validated into the spine

**What the spec says.** Stage 1 is "things that emit a dispatch table", and `external.py` is "client-supplied snapshots (CSV/Excel) → same table". The words that matter are *same table*: the stage-1 → stage-2 contract is the dispatch table keyed by detailed-grid unit id, and an external producer must land on exactly that contract or the cage leaks — everything downstream already speaks detailed ids only.

**The design.**

1. `producers/external.py` reads a CSV or Excel dispatch and returns the same object `pypsa_nodal` returns. The unit set is checked against the detailed grid's registry **in both directions**, exactly as `from_network` is (increment 5, D3): a unit in the file that the grid does not have is an error naming it, and a grid unit the file never mentions is an error too, because a partly-specified dispatch would rank a grid that is not the one being studied. That symmetry is the whole reason `from_network` was built that way and there is no case for weakening it here.
2. Column contract fixed in the module docstring and enforced with a typed error: unit id, hour, P (MW), Q or a power factor, plus per-hour committed/available flags where the ranking metrics need them. Anything ambiguous is refused with the offending rows named, never coerced — an external file is exactly where a silent coercion becomes a wrong study.
3. Non-convergence and infeasibility stay *results*, not crashes, per the spec's error-handling rule; a malformed file, by contrast, is a stage failure and writes the typed error artifact the UI and copilot already render.
4. `StudyConfig` gains `from_external: Path`, mutually exclusive with `from_dispatch` and `from_network` — the existing pairwise check in `study.py:58` becomes a three-way one, and `to_dict`/`from_dict` carry it.
5. Backend: a fourth dispatch source in `gridspine_service`, going through `_authorized_dispatch_dir`'s ACL path like the others — an external file is an upload, so it lands under the study's `uploads/` with a sanitised basename, the way increment 6's read-back upload does.
6. Frontend: a fourth radio in the dispatch-source picker, with the file input; chat: the source becomes an argument the existing `gridspine_create_study` already shapes, so no thirteenth tool.

**Amended 2026-09-10, after A1 landed: demand is part of the source.** The plan
as first written missed this, and building A2 surfaced it. An external dispatch
carries only the GENERATION side, and neither route that exists elsewhere
applies — a PyPSA network brings its own loads (`tables_from_network`), and a
generated year synthesises them from `LOAD_SHAPE` (`dispatch_year`). Pairing a
client's generation with gridspine's synthetic demand would leave the mismatch
to the external grid's slack: the load flow converges, and its flows and N-1
severities describe a grid state that never existed. The inertia and IBR-share
metrics would still mean something, which is what makes it dangerous rather
than obviously broken.

So the source is TWO tables, and the absence of demand is a refusal rather than
a fallback — the owner's decision, taken over a ledgered synthetic fallback and
over a rank-but-do-not-screen variant. Either two files, or one Excel workbook
with `dispatch` and `loads` sheets, because a client exporting one file is the
common case and making them split it invites exactly the hand-edit the alias
tables exist to avoid. The two tables must also cover the SAME hours: demand for
an undispatched hour, or a dispatched hour with no demand, is a half-specified
snapshot the slack would quietly balance. This widens A2 to two uploads and A3
to two file inputs.

**Evidence a headless session can produce.** Every one of these is a real gate here: `gridspine-tests` for the producer and its refusals, `gui-tests` for the service and router, `vitest`/`tsc` for the picker, and a vertical slice that runs a study end to end from a hand-written external CSV against the IEEE 39-bus template — the same shape the increment-5 `from_project` slice uses. A deliberate mutation per task, per the standing discipline.

**What it will NOT prove.** That a real client export parses. The fixture is written here, so it is the *shape*, not the evidence — the same honest limit increment 6 recorded for the PowerFactory bundles. That gap closes when a client file lands, and the plan should not pretend otherwise.

## Tasks

- [x] A1 gridspine (`1eedeb82`, demand required in a follow-up commit): `producers/external.py`, the two-directional registry check, the typed column contract for both tables, the required loads table and the hours-agreement check, `StudyConfig.from_external` and its three-way exclusivity. 26 tests. Three mutations, each killing exactly its intended test: dropping the omitted-unit direction (only the "never mentions" test — and note the per-hour completeness check does NOT cover it, so that direction is load-bearing, not belt-and-braces); leaving `from_external` out of the exclusivity list (only the two parametrised exclusivity cases); disabling the hours-agreement check (only the hours test). Gate: `gridspine-tests` 593 passed / 2 skipped at A1, re-run after the demand change.
- [x] A2 backend (`8475c372` driver seam, `8864dab1` service/router): the fourth source in `gridspine_service` through the ACL path, upload storage with a sanitised basename, the driver's refusals as 422; router and chat argument rows. Mutation: an unsanitised upload name must turn the escape test red (increment 6 found this one needs a path-shaped name ending in `.csv` to bite).
- [x] A3 frontend: the fourth picker option and its file input, thin, last. Mutation: a picker that ignores the new source must turn exactly one test red.
- [x] Vertical slice: a 39-bus study from a hand-written external CSV, ranked, bundled. 6 tests in `tests/gridspine/test_external_slice.py`, against the REAL grid rather than the stubbed registry the other tests use. Screening is on, which the plan did not anticipate mattering: `year_study.py:583` writes the full `bundle_h<hour>/` only in the second pass that screening gates, so a slice with screening off (as the increment-5 `from_project` slice has) proves the stages run and NOT that the deliverable appears. Verified output for the selected hour: a 14.9 kB PSS/E v33 `.raw`, a `.dyr` carrying GENSAL/GENROU records, `contingencies.csv`, `fault_levels.csv`, `ledger.json`/`.md` and the load-flow tables. Mutation: dropping the demand path where `run_study` calls the driver errors all six. The fixture's unit ids and load buses are derived from `registry_from_net` and the net's own load table rather than typed in, so it cannot drift out of agreement with the grid it is checked against — which is the thing the producer tests.
- [x] Demand buses checked against the grid in both directions, with per-(bus, hour) completeness (the amendment below). 5 gridspine tests, 1 backend test, 2 mutations.
- [x] Gates before each path-limited commit: `gridspine-tests`, `gui-tests`, `npx tsc -b`, `npx vitest run`.

## Amended 2026-09-12, after the increment landed: the demand buses were not checked

A1's two-directional unit check was taken straight from `tables_from_network` and
is right. The DEMAND table got no equivalent, and nothing above noticed — the
plan says "the unit set is checked against the detailed grid's registry in both
directions" and the registry is units, so the gap was written into the plan and
then faithfully implemented.

What it cost. A loads table naming a bus the grid does not have, or omitting one
it does, passed the producer and therefore passed `check_external` — the function
whose entire reason for existing is that a client learns at the UPLOAD that their
file is wrong. The run still failed closed, but late and in the wrong voice:
`ranking.metrics.snapshot_metrics` sums `p_mw` over every row it is handed, so
`load_mw` was already wrong by that bus's MW before `severity.hourly_dc_flows`
(unknown buses) or `loadflow._apply_loads` (both directions) refused the table at
the ranking stage, with a message about the DC artifact.

The fix: `load_buses` is a required keyword argument of `tables_from_external` —
required, with no default, because a caller that forgot it would reopen exactly
this hole in silence — and `_check_load_buses` applies the same two directions
plus per-(bus, hour) completeness, the completeness half factored out of the unit
check into `_check_every_hour` since the hazard is one hazard. The driver derives
the set with `_demand_buses(net)` from `net.bus["name"]` indexed by
`net.load["bus"]`, the same indirection `to_loads_table` uses. NOT from the
registry's `bus` column: that is where the machines sit, and on a real grid
neither set contains the other — the test fixtures keep the two deliberately
disjoint (demand on L0/L1, generators on B0/B1) so an implementation reaching for
the registry turns every happy path red rather than passing by coincidence.

Evidence: 5 new tests (4 in `tests/gridspine/test_external_source.py` — both
directions, per-(bus, hour) completeness, and the registry-discrimination one —
plus `check_external` refusing at the seam) and 1 in
`pypsa-gui/backend/tests/test_gridspine_external.py` (a 422 naming the bus, with
the config left unset). Two mutations: dropping the loads completeness call turns
exactly `test_a_demand_bus_given_for_only_some_hours_is_refused` red; checking
demand against `registry["bus"]` instead turns 12 red, including every happy path
and the discrimination test.

## The end-to-end review, 2026-09-12: what three independent passes found

Three reviewers were run over the increment with no access to each other's
conclusions: one on the data contract (producer, driver, schema), one on the
upload path as a security surface, one on the picker plus the pandas-3 guard.
Each was asked for findings with a concrete failure scenario and for a list of
what it checked and found sound. Everything below was re-verified here before
being acted on — two of the pointers were reported as more severe than they
turned out to be, and one turned out to be more severe.

### Fixed, with a test and a mutation each

| what | where it bit | commit |
|---|---|---|
| demand buses unchecked in both directions | `check_external` passed a bus the grid lacks; refused three stages later, after the ranking had already used a wrong `load_mw` | `185f482e` |
| `create_study` accepted `from_external` paths | the `from_dispatch` path-injection class, reopened on a field added after the guard; the refusal message leaks the target file's columns through the job queue | `d058de45` |
| bytes stored before validation | a refused re-upload overwrote the accepted source the config points at | `d058de45` |
| the collision guard's own case | a dispatch named `loads.csv` was overwritten by the demand and validated against itself; `upload_readback` had it too | `d058de45` |
| no filename length cap | a 5000-character name was a 500 with a traceback | `d058de45` |
| uploads parsed on the event loop | a crafted workbook (14 s measured at 4.5 MB; the cap permitted ~115x) stalls every other request | `d058de45` |
| no queued-job lock on either source route | a queued study's source could be swapped after it was queued; the GUI's disabled picker was standing in for a backend check that did not exist | `d058de45` |
| a timestamp `hour` | became epoch nanoseconds — `bundle_h1704067200000000000/` — while the same column in a CSV was refused | `0b32be97` |
| `hour = 1e19` | wrapped to INT64_MIN with no warning | `0b32be97` |
| a literally duplicated header | pandas renames it to `p_mw.1`, so the ambiguity guard missed the one case a hand-edited export actually contains | `0b32be97` |
| `to_loads_table`'s missing direction | the nodal path kept the late refusal the external producer had just closed | `0b32be97` |
| a stale `error_<stage>.json` | made the NEXT, successful run report `failed` over a complete manifest | `0b32be97` |
| two hour counts in one manifest | `"hours": 2` beside `"config": {"hours": 8760}` | `0b32be97` |
| picker state outliving its inputs | after a mode round trip both file inputs read "No file chosen" while Apply re-uploaded them | `6a09727f` |
| a demand file sent with a workbook | the server took its two-file path and read sheet 0 — possibly the loads sheet — as the dispatch | `6a09727f` |
| a refusal outliving its source | a red paragraph about a spreadsheet under a successfully applied network | `6a09727f` |
| no client-side suffix check | the server renamed an unrecognised suffix and the producer refused a filename the engineer never used | `6a09727f` |
| **the snapshot did not have to close** | the one finding bigger than the increment: see below | `34cfc5fd` |
| RES `q_mvar` dropped by the load flow | validated, written to `dispatch.csv`, then lost — `net.sgen` is PQ, so Q is an INPUT there | `34cfc5fd` |
| a mixed artifact pair | a failure between the two writes left this run's demand beside the previous run's dispatch, and `resumable` said True | (this commit) |
| a table capped at a network's budget | two 512 MB parts buffer ~2 GB before a byte is validated | (this commit) |

### The finding that was larger than the increment

Requiring a loads table (the amendment above) stops gridspine from INVENTING
demand. It does not stop a client's two tables from disagreeing with each other —
and nothing checked. Measured on the real 39-bus grid: a dispatch at 90% of its
own demand was accepted, converged with **+625 MW arriving through the external
grid's slack**, and produced flows at 150% loading — while `import_mw`, the
ranking criterion whose whole purpose is "greatest reliance on the external
grid", read **0.0**, because it sums what the client wrote on the ext_grid row
rather than what the slack did. Every N-1 severity, fault level and `.raw` in
that study describes a grid state the client's tables do not.

This is the same argument the increment's own amendment makes for requiring
demand, applied one step further, and it was missed for the same reason: the
plan reasoned about where demand COMES from and not about whether the two
tables agree.

The fix refuses a gap larger than losses could explain — 5% of the hour's
demand, with a 1 MW floor, stated in the module as a LEDGER ASSUMPTION
(transmission losses run 1-3%) rather than a tuning knob. It does not forbid
imports: the producer already requires a row for every registry unit, the
external grid's included, so a client who means to import declares it there and
the snapshot closes. The refusal names the hour, both totals, the signed MW, the
percentage, and how to declare the exchange. The worst gap is carried into the
provenance record as `max_imbalance_mw`, so a study that closed to within 4% is
traceably different from one that closed exactly.

The stub fixtures had to be rebalanced to land it: they were 200 MW of
generation against 160 MW of demand. A 25% imbalance was the happy path of every
test in the file, which is its own small lesson about fixtures that are written
to exercise a code path rather than to describe a grid.

### Reported and deliberately NOT changed

- **`defusedxml` is not installed**, so openpyxl's XML-bomb guard is off
  (`openpyxl.xml.DEFUSEDXML is False`). Adding it is one line in `pixi.toml`,
  but it re-solves the lockfile for four platforms and collides with the pandas
  bound on `fix/pandas-upper-bound`; with nothing retained on refusal and the
  parse now off the event loop, the remaining exposure is one request's CPU and
  memory, which the 64 MB cap bounds. Worth doing when the two branches have
  landed: `pixi add defusedxml`.
- **`get_config` still returns the raw server paths** of the uploaded tables
  beside their basenames. The frontend reads `from_external` as the
  "is a source set" signal, so removing it is a UI change rather than a
  redaction; and with `create_study` closed there is no route that turns
  knowledge of the path into access. Accepted, named here.
- **The per-part cap is per part**, not per request: two parts at 64 MB each can
  still buffer 128 MB (256 MB at the `b"".join`). A per-request budget belongs in
  `upload_guard` where every import endpoint would get it, not bolted onto this
  one route.

### What the reviews found SOUND, which is also evidence

Worth recording, because it is the half that says where the increment was
already right: `_safe_table_name` against 25 path-injection spellings (dots-only,
suffix-only, NUL, RTL override, homoglyphs, Windows separators, `~`) — nothing
escaped `external_dir`; the project-kind gate running before any directory is
created; the ACL identical to its eleven sibling routes and applied before any
write; `update_config` unable to set either external field; source exclusivity
enforced n-way rather than pairwise; `is_file()` being what stops `pd.read_csv`
from fetching an `http://` config value; every malformed-input class probed
(thousands separators, decimal commas, Excel dates in `p_mw`, timedeltas, BOM,
semicolon delimiters, mixed dtypes, non-tabular bytes) arriving as a
`ContractError` and never a traceback, so the backend's single
`except ContractError` holds; `check_external` writing nothing; and the
`_demand_buses` indirection agreeing exactly with the key `_apply_loads` matches
on.

## Out of scope, named

The connection-study driver (`drivers/connection.py`); the grid-strength expansion beyond the SCR that `static/strength.py` already computes; the compliance rule engine; the PowerFactory API exporter; PowSyBl ingest; the clustered producer. Each is either behind one of the spec's own revisit triggers or behind a resource this environment lacks, and every one of them is a larger piece of work than A.

## The four owed items, and what would unblock each

Recorded here so they are not silently carried forward a third time:

- **ADR 0002 live probe** — needs `ANTHROPIC_API_KEY` in a session. Nothing else. It is the smallest of the four and the only one gating a claim already made in the PR description ("the copilot can do the same things through the same functions"), which is today structurally true and behaviourally unverified.
- **Browser pass on the study view** — needs a display; Chromium and Playwright are already present in this environment, so a headed run is closer than the other three.
- **Real PowerFactory export** — needs a licensed session; the runbook in `tests/gridspine/fixtures/powerfactory/README.md` says exactly what to export.
- **macOS `.app`** — needs a Mac; the two risks are lightsim2grid's binary under the hardened runtime and its code signature.
