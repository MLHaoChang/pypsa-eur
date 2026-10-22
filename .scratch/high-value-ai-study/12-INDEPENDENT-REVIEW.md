<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PR #567 final independent implementation-spec review

**GO for implementation-spec readiness only, on frozen native source `880496ef679af1efc51a6dd3d62f9e41a75f2937` and the exact normative files hashed below. No unresolved blockers.** This supersedes both the earlier candidate NO-GO and this reviewer's earlier GO against the older source. The material 174-file source delta was reassessed; approval was not carried forward automatically. This is not runtime, CI/premerge, merge, production or independent domain approval.

Candidate base commit is `11afe14a2b02193715d26d04907aa77051efb5b3`; final fixture corrections and frozen-source amendments after that commit are included in the disk hashes. The immutable hashes govern this verdict. Subsequent relevant source/schema changes need the normal affected-scope reassessment; this review makes no continuous-current-master guarantee.

## Independent reassessment and findings

I read the previous findings, amended 08/09/10/11, machine plan, validator, OWNER disposition and applicable AGENTS/steward/harness instructions. Initial native source was `7a14d27038de424d07a832b16e2465297c7b8b7d`; the intermediate `1b1aa5ccc5dd4c8ca9fdf2ec680fd96cb5f687b5` money-year delta remains separately recorded. I inspected the relevant later native harness/finance/equipment/edit-card/scope/results/typed-cost/owner changes through `880496ef6`, verified that all 174 changed paths are pinned and that all 14 family native anchors exist on the frozen source.

- **F1 closed:** LC-23 S1/S2 and delivered foundations are separated from residual work. Existing `paused_at_card`, `continue_task_id` and `continue_at_card` are retained. Exact boundary/semantic revision is consumed once under native cross-process checks; stale/delayed/cross-session or revoked boundaries cannot grant later authority. Each subsequent write/run needs its own current click.
- **F2 closed:** diagnostic validation/read/draft/tool/provider bounds, durable pre-debits, task-wide allowances across attempts/reconstruction, lower-limit precedence, equivalent-failure stagnation and truthful partial termination are explicit. SDK retry/fallback attempts count; unknown usage and goal/task churn cannot replenish allowances. Waiter observation does not establish solver completion.
- **F3 closed:** normal finish, late receipts, automatic settlement and reconciliation share the family-native proof requirement. Exact origin/attempt/revision, atomic receipt/proof/status, positive native quiescence/fencing, crash reservations, legacy records and compatible-reader cutover are specified. Missing proof cannot advance dependent effects or authorize replay. New equipment-card digest binding is retained as a delivered foundation, not replaced.
- **F4 and residual closed:** 53 planned cases have 53 individual parameterized nodes; 22 fixtures include 14 distinct family setups/oracles/native anchors. J09 is feasible with firm 4 MW and objectives EUR 140/150, delta EUR 10. J03 uses the correct direct `upload_load_profile` foundation, a two-row UTC-naive 2030 axis, explicit staged identity or refusal and no measured-calendar claim; J10 inherits that bounded path. Native timezone stripping/duplicate-axis normalization are preserved/disclosed. A matched L1 +inf cell exercises the actual 422 refusal before writes. E2E-02/E2E-06 select the exact generator edit and Class C cold-snap adequacy operation.

The frozen-source amendment correctly adopts shipped `investment-lcoe/1` alongside `investment-case/1`. It retains the engine SAM-generalised definition, per-year energy/value/equity inputs, exact headlines, recorded rate/basis and opt-in status. Old reports without retained inputs stay unavailable; unknown/malformed series are not fabricated, and no solver rerun is implied. Payback, cover ratios and solved PPA remain withheld. I caught and the author corrected an ambiguity between the real-basis NPV/IRR fixture and the separate nominal-basis LCOE fixture: only the latter uses inflation 2% and a real-energy discount rate of 4/51. Its independent nominal oracle is 205/21 and real oracle 25625/2703 currency/MWh; a real-price-basis report instead has real equal nominal, as native code specifies.

The amendment also preserves the manual equipment mapping form/read-only preview and under-lock approved digest/value/readback; matrix-driven run availability; existing grouped results/campus/connection navigation; and native typed-cost capacity basis, money year, grid-connection inclusion, conversion and legacy-pricing refusals. A checked investment report adapter cannot make an unknown/non-runnable scope kind runnable. Displayed result freshness is not checked proof. LIFE-CO2 is the delivered read-only placement slice, and PACE-3 authorizes only its named follow-ons.

## Verification and evidence limits

The offline validator passed after the final frozen-source amendments: `python -B .scratch/high-value-ai-study/validate_specs.py`, exit 0. It checks pins, matrix/manifest identities, anchor existence and separately attributed JUnit/hash/source records. The final skip handling admits only the exact existing `test_every_known_gap_is_still_a_gap[NOTSET]` empty-parameter skip and its declared reason; other skips/failures remain rejected. I independently checked S7/S8/LCOE/adequacy/tariff/J09 arithmetic and reverified byte counts and SHA256 for all seven saved official Claude Code documents. These are consistency and arithmetic checks, not execution of the proposed journey cases or private-Claude implementation knowledge.

Keep existing execution evidence separate:

- Author 142 foundation tests on 7a, zero failures/errors/skips.
- Previous independent reviewer 42 shipped goal/budget tests on 7a; not new LOOP execution.
- Author 30 incoming money-year tests on 1b, zero failures/errors/skips.
- Author 880 batch: 448 collected, **447 passed, one existing empty-parameter skip**, zero failures/errors, 139.887 s. The JUnit/hash/source and exact skip were independently validated. This replacement reviewer did not rerun these native suites.

Never sum these counts, transfer source labels or call them universal coverage. **Zero of the 53 proposed harness acceptance cases has executed.** The advisory money-year warning establishes neither normalization nor a common basis; hand-typed replacement years remain unknown. The final 11 source labels now explicitly keep 142 tests and their inventory on 7a, with the latest inventory 279 tools/17 result tabs/23 panels separately on 880.

Implementation must satisfy the mapped positive/adversarial cases, cross-process proof/counter/boundary contracts, crash fences and compatible-reader cutover in the existing owners. Family evidence stays bounded by its exact oracle and exclusions. Actual implementation-head checks, independent domain review, live calibration and production decisions remain separate gates. These are retained delivery conditions, not unresolved specification blockers.

No paid/model calls, standing-reviewer contact, runtime/spec edits by this reviewer, push or merge were performed. Only the final review artifacts were written.

## Immutable reviewed files

Paths below are relative to `.scratch/high-value-ai-study/`. Self-updating `current-master-reconciliation.json` and the final review mirror 12 are excluded from the immutable hash set.

| File | SHA256 |
|---|---|
| 08-HARNESS-IMPLEMENTATION-SPEC.md | `e0c6ec7e0a2a575703c713c4daa2f7d1a3ac86d86a130e091f5731199ba0d97f` |
| 09-HARNESS-DATA-AND-STATE-CONTRACTS.md | `98b197e9b4b9ab3aafd44fc6d6db5a7bb271db5f5f266fb8442c8377994ed713` |
| 10-HARNESS-DELIVERY-AND-TESTS.md | `87f088c574abe75ff53d47a6b72481191da171a00e15bfe9a4fbc043df6c6d63` |
| 11-CURRENT-MASTER-AND-REVIEW.md | `a7dbbc4912716c2a671106644be278a1b4ad22a15fe227024a18d76b60247bc3` |
| harness-implementation-plan.json | `fbb3c0994f99f3d22c8e726ed3cb10785f6fb1f74435b4124984fce39ff74b64` |
| validate_specs.py | `5c6a036402c366d69f582c7457a18ede142c1a430e2281b0413eb6fbf7adcf0a` |
| OWNER-DISPOSITION.md | `9cf576de6906342828fdf2b70c992698f7a73a5dbb965df1dcfe8527009f9d14` |

Machine-readable verdict: [independent-review.json](independent-review.json).
