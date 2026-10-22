<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Active implementation plan after the next master update

Source snapshot: `4b57cb3ccf310b151e53f4344015e642db0ca0cb`. This amendment governs delivery over the earlier880496ef6 assessment. The original08/09/10 contracts and53 planned acceptance cases remain historical, source-bound records; their unchanged guardrail requirements carry forward, while the delivered-status and priority changes below take precedence. The880 independent GO is not silently relabeled onto this source. See [active plan and evidence](active-master-plan.json) and [current independent verdict](14-ACTIVE-PLAN-REVIEW.md).

## What master already added

| Delivered change | Preserve exactly | Remaining implication for this plan |
|---|---|---|
| PDF datasheet-to-asset values (#591) | Exact page/quote, native number parsing and field/unit/currency checks, bounded child-process PDF parsing, reviewed edit card and native readback; no OCR or arbitrary document execution | Native numeric asset extraction is delivered. Keep the Expert quote-picker/dialog follow-on separate; do not rebuild the backend or claim arbitrary documents/fields work |
| PDF tariff/PPA values (#594) | Existing unpinned tariff items/contracts only; quote-bound currency/year, sole admitted /MWh→per-kWh conversion, commercial config compare-and-set, preflight and exact readback | Commercial extraction is delivered within that scope. Its receipt says network undo does not revert solver-config changes; restore old values explicitly. Native completion proof must preserve this family distinction |
| Checked COPT screening (#483) | `copt-screening/1`, classical two-state fleet,1MW capacity grid, generator-weighted horizon LOLE/EUE and hourly LOLP; unsupported profiled/folded/period-varying/off-grid fleets refuse | Reuse the shipped independent reconvolution. It excludes storage, imports and network physics and is not a statutory annual reliability claim. It is distinct from the Class C stress oracle in E2E-06 |
| Checked charts and answer presentation (#486/#487) | Chart series come only from that answer's exact evidence/window; missing series are named, no invented/resampled data; retain per-series/point bounds and download integrity | Chart creation/presentation is delivered. At render time source must be current; after evidence expiry the download is explicitly historical and not freshly re-verified. No universal current-evidence badge |
| Display-to-answer join (#488) | Checked loading receipt can report the same solve/window as the browser's native ACK; wrong/unconfirmed/PF source stays a navigation card note and does not change the checked-answer verdict | Preserve the join. It does not prove identical displayed values or upgrade/downgrade the checked-answer verdict; exact data hash proof remains adapter-specific |
| Scope-owned sizing, load flow and N-1 (#598/#612/#615) | Sizing off dispatches fixed capacities; load flow and N-1 are controlled by scope, with read-only solver-setting state and native run-separability/readiness | Do not propose another standalone load-flow UI/button or assume every run kind is separable. Every run still uses native admission and its own approval/evidence |
| AC-PF and LCOE corrections (#609/#484) | A second PF is refused and a failed PF restores the LP tables; the nominal-price-basis real-LCOE denominator must be positive. Real-price-basis real==nominal behavior is unchanged | Retain numerical regression gates; provenance alone does not validate physics. Payback, cover ratios and solved PPA price still need their own checked adapters |
| Profile/time-series work (#586/#603/#605/#606/#616) | Shared series editors, source/readiness labels, explicit snapshot/year/step aggregation rules, bounded batch preflight/readback and native partial-failure receipts | These foundations are delivered. Batch preflight refusal and a failure after some writes are different outcomes; do not call every batch atomic or measured. Native time normalization and generated/assumed provenance remain explicit |
| Flow navigation, connection limits, template changes and owner decisions | Native return-to-origin navigation, readiness-limit editing, honest ammonia prices/retuning and named package exceptions | Reuse their current destinations and native availability; no new assistant/progress area. New owner exceptions authorize only their named work |

PR#621 at this snapshot changes coordinator/UX IDs and ROT-1 records; it is **not** a branch-port-loss fix. The branch-calculation delta extracts the existing utilization-series helper for charts, not a port-loss correction. Leave any such unresolved physical limitation pending until an actual native fix/test exists.

## Residual priorities after removing duplicate work

| Unit | What remains |
|---|---|
| HS-01 first | Discovery still exposes only `goal,limit,overview`, clips search to at most8 results, and returns field names rather than complete version-bound schema pages. Add bounded pagination/inspection first; then exact service/component support and route/control dispositions |
| HS-02 | Shared native proof on normal finish, late receipts, automatic settlement and reconciliation; positive origin/fence after restart, durable proof/legacy migration. Incorporate the delivered asset/commercial PDF receipts, without inventing universal undo |
| HS-03 | Source-revalidated working packet and exact byte/tool-pair/required-intent packing. Existing append-only history and ownership remain authoritative |
| HS-04 | Coverage/plausibility and interpretation guards beyond the newly delivered checked profiles/charts. Missing claims stay unavailable; broad compliance/dynamics/finance recommendations are not established by these adapters |
| HS-05 | Exact current-boundary Continue binding, durable bounded validation/correction and stagnation, remaining plan-card/hand-off and per-family settlement adapters. No second task runtime or implicit effect retry |
| HS-06 | Case-level source/schema/artifact evidence linkage and cross-family adversarial journeys; inventory counts do not establish behavior. Preserve new regression suites alongside planned cases |
| HS-07 | Trusted procedures using actually available tools and explicit stop conditions; no general shell, automatic hook installation or agent-team runtime |

Nine central task/discovery/context/budget/loop blobs are unchanged from880; all14 existing family fixture anchors still exist. The changed native APIs therefore expand what must be preserved and tested but do not eliminate the discovery/proof/context gaps. The active JSON maps those source checks and regression evidence separately from historical test counts.

## First bounded build: HS-01a

Extend **existing** `find_capabilities` additively with `offset=0, details=False, schema_sha256=None`. Preserve legacy defaults/result fields and discovery's read-only status. Search pages must advance by the actually returned count under the3800-character bound; exact-tool details return complete native catalogue field constraints and a schema digest. A subsequent nonzero detail page requires that digest; changed metadata refuses rather than mixing schema versions. Internal definitions are resolved only under the existing proposed finite depth/cycle rules; oversized/unsupported fields refuse explicitly. An ambiguous tool query clarifies before schema expansion. No new endpoint, callable capability, provider-specific path, approval, or effect is created.

This first slice covers **catalogue tool schema metadata**, not all Pydantic service cross-field rules, component instance resolution, arbitrary monetary units, or full route/GUI disposition coverage. Those stay in HS-01b. Even a valid JSON schema is not admission authority; native handlers and current ACL/lock/restriction/approval checks still run. The optional current-eligibility hints remain observations.

Code targets are `services/assistant_tools.py`, existing catalogue declaration/handler parity, typed error manifest and focused discovery tests. Add genuine failing behavioral tests before code. Required assertions: unchanged legacy calls; stable no-gap/no-duplicate search paging and cursor progress; exact schema constraints and hash-bound detail pages; bool/range/malformed cursor rejection; ambiguous/oversized/cyclic/external-ref behavior; zero writes/solves/provider calls by discovery. Retain existing offered-tool cap and task controls. Run focused discovery/schema/endpoint/error-manifest/provider-offer suites plus the three chat semantic suites and inventory/control-matrix checks. Do not mark all CAP cases or all HS-01 done from this narrower slice.

## Validation and approval meaning

Current-source regression results and exact JUnit/hash are recorded in the active JSON when complete. Earlier142/42/30/447-pass records remain on their original commits and must not be summed or relabeled. The53 future harness acceptance cases remain planned and unexecuted as complete new journeys. Native/scripted regressions do not measure live language quality. Independent domain review and real-use calibration remain pending by owner choice; no paid provider/media calls or standing-reviewer contact occurred.

Applicable implementation/UX/engine gates, package ownership and exact-head CI/premerge still apply. This reassessment updates the existing specification PR; it does not merge, deploy, start paid experiments, grant production use or claim all domains correct.

On the stated4b57 source,15 existing native/regression/semantic suites completed with **443 passed, one existing empty-parameter skip, zero failures/errors** (159.577s). The sole skip is `test_every_known_gap_is_still_a_gap[NOTSET]` because its known-gap parameter set is empty; no required behavior was disabled. Source/hash/JUnit and command are in the active JSON. Inventory/matrix checks passed:287 tools/17 result tabs/23 panels,170 controls/14 families; full coverage remains not established. This validates selected shipped foundations, not the53plannedfuturejourneys, frontend E2E or population reliability.
