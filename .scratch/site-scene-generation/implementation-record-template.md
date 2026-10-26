<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assignment, evidence and successor handoff template

Status: blank template; unchecked fields and examples are not evidence

Copy into the bounded implementation PR's durable record. Use repository/GitHub links, not required local-only paths. Never include tokens, customer photographs, EXIF or private absolute paths. Record no assumption as an accepted decision.

## Assignment contract — fill before code

- Unit ID and linked ticket / spec full SHA:
- Actual master full SHA / implementation branch / initial and current full head:
- Accepted prerequisite merged SHAs and decision records (or blocked reason):
- Exact supported behavior and explicit exclusions:
- Owning existing paths/symbols and approved changed-file allowlist:
- Open-PR overlap assessment; plan review reference(s) required by current rules:
- Frozen public method/path/DTO/error/unit/safety/permission and expected-revision contracts:
- Applicable V IDs with individual assertions, planned test nodes and fixture/oracle IDs:
- Generated outputs to regenerate and exact command (inventory/control/route/error manifest as applicable):

## Test-first and result evidence

| V ID / assertion / unit | Fixture hash and independent expected value | Test node / command / source SHA | RED failure evidence | GREEN result evidence | Exclusions / status |
|---|---|---|---|---|---|
| Fill one row per assertion | Do not derive expectation from projection under test | Record environment and exact invocation | Expected failure and exit status before implementation | Actual output, counts and exit status | planned / blocked / failed / partial / passed |

A range of cases or green job title is not enough. Expand parameterized ownership/revocation, geometry, unknown-vs-zero, cadence, identity, crash and privacy assertions. Native-only testing marks optional-engine assertions not_run with reason; it cannot mark the whole mixed-engine V row passed. Sum neither overlapping local suite counts nor prior-head results.

- Focused suite and relevant unchanged regressions, with exact interpreter/toolchain:
- Required chat-tool semantic, inventory/control/route/error checks when touched:
- Full applicable backend/frontend/tsc and exact-head remote runs/jobs/actual steps:
- Master failure reproduction if needed (same test/command/environment), without weakening it:
- Guided/Expert/manual/no-provider screenshots/browser steps, synthetic input links:
- Packaged-device profile and measured runtime/resource thresholds:
- Genuine optional-engine evidence, versions/inputs/artifacts, or explicitly unavailable:
- Source/license/privacy review and artifact closure verification:
- Denial, stale revision, revocation, cancellation, unknown outcome/read-back evidence:

## Completion and release boundary

- Actual implemented units/behaviors and remaining assertions:
- Applicable engine/UX/GK review references with full reviewed heads; historical/stale verdicts identified:
- Latest exact-head CI / fresh premerge against actual master; blockers or expected path skips:
- Release fragment path and user-visible capability flags/readiness; optional features kept unavailable:
- No claim of broad engineering compliance, real-provider quality or independent calibration without separate evidence:

Only mark a unit accepted after its required implementation evidence and applicable review/merge gates are satisfied. A prototype has accepted contracts/measurements, not a shipped runtime. A merged PR is not automatic proof that other units or optional engines are complete.

## Successor handoff — replace with live state at each stop

- What is pushed: PR URL, branch, full head and base; what is local/uncommitted:
- Files read/changed and accepted decisions; any contract amendment still awaiting review:
- Last command, output/evidence link and exit status; running processes/suites if any:
- Exact next action / first failing test / blocker; units explicitly not started:
- Outstanding owner/coordinator/review gates and who routes them under current rules:
- How to fetch evidence without private local paths; no statement that another agent was contacted unless actually sent:
- Skipped work and the risk it leaves:
