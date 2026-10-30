<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# HH-07 — Verified research and user-approved parameter proposals

Status: needs-triage
Type: detailed implementation specification; documentation only in this commission
Baseline: `master` `60d8332d64b2edef1b204fb5980311e6102229a8`
Depends on: study-harness 09 and its 04/03 prerequisites; HH-01/02/03/04/05; HH-06/08/09 for integrated journeys; HH-10 before automatic solves

## User outcome and boundaries

The owner can ask the study assistant to research a missing parameter, inspect traceable candidate values and ranges, choose a value, approve an explicit old→new card and continue the native study. The report retains what was searched, what the sources actually said, which assumptions remained uncertain, what the owner selected and which results used that selection. The assistant may explain options; it cannot turn a plausible-looking citation into verified engineering truth.

This completes the interface of existing study-harness [09 research step](../../study-harness/issues/09-research-step.md) and design §8. It does not introduce a general browsing agent, background literature crawler, new research mode or direct model-write path. Use the existing profile's optional research capability, source ledger and run card. Existing five workflow cards remain capacity, grid, control, reliability and commercial. A profile declares which gaps are researchable and which native target/admission adapters they need. Choosing a workflow card alone grants neither search nor domain coverage.

Source verification means the server retrieved the cited resource and matched the quote to retained content. It does not establish that the author is correct, current, representative, applicable to this site or independently validated. A source ranking and a provider's self-confidence cannot certify a value. Research does not change native plausibility rules, HH-02 constraints, solver settings or the scientific question to obtain a pass. Objective improvement after a researched price change is a changed assumption, not a better physical design.

## Source audit and existing seams

Read root/GUI `AGENTS.md`, `backend/harness/README.md`, `TESTING.md`, existing study-harness design and issues 03/04/05/09 before implementation. There is no tracked `GUIAGENTS.md`. The external source-notes audit is stale baseline context; the sources below define this ticket's observed implementation.

`services/run_profiles.py` currently registers only `eh_site@1`; its envelope explicitly has research disabled. It describes site inputs, but there is no implemented research-turn capability in `harness/protocol.py`: `LLMRequest`, `LLMEvent` and `LLMProvider` currently expose ordinary tools/messages/usage. Do not assume a provider supports web search because its name is familiar. `harness/providers` is the only place for wire-specific capabilities and translation.

`services/run_envelope.py` already validates `research={allowed,cost_cap_eur}`, with EUR2 default and EUR10 hard cap, and refuses researched-value matches through `RESEARCH_KEYS` / `research:` provenance. Its research exclusion must remain effective when model arguments omit those labels; the new source ledger needs server-owned target/proposal provenance. The existing issue09 bullet saying `cost_cap_usd` conflicts with its own acceptance criteria and shipped EUR implementation. EUR is canonical; do not add a USD alias or silently convert an old USD field. Existing profile research disabled is preserved until explicit reviewed activation.

`harness/runner.py` already executes in the background, stores model-cost use and stops on missing usage/rates. HH-03 replaces coverage claims with prepared-call bounds and physical-attempt reservations; current after-call charging is not a hard search-cost bound. `harness/fence.py` and `eventlog.py` already handle untrusted/model-visible content. Reuse them rather than append source text to a system prompt or create a second transcript.

`harness/edit_card.py`, `approvals.py` and `effects.py` provide old→new previews, fresh confirmation and native readback. Every researched value requires a fresh explicit user card in both Guided and Expert, even within an approved envelope. Research permission, source selection, Choice answers and study-brief approval are not that card. For decision-study rows, use existing `services/study/ledger.py::apply_user_row` through its real approval/effect path and preserve exact units. `services/run_evidence.py` owns immutable result evidence, while study ledger/report artifacts retain assumptions; do not copy external quotes into numerical evidence and call them checked calculations.

Native plausibility is K#851's future native adapter; checked findings remain Assurance issue10's separately gated scope. Research adds no checked `get_results` permission. [HH-09](09-coordinator-delegation.md) owns `IntentContractV1`, `AcceptanceItemV1`, `AcceptanceCoverageV1` and `DecisionPacketV1` in `harness/decision_packets.py`; this module records `(intent_id,intent_revision,intent_sha256,acceptance_id)` for every research item and leaves checker/human-review closure to that contract. HH-02 is only its supported confirmed native input predicates. [HH-08](08-report-workbook-revisions.md) reports the same acceptance IDs and source/proposal revisions.

## Closed registration and first slice

Register a `ResearchGapAdapter` in the existing `run_profiles.py` capability data, not a model-defined tool. Fields are `id`, `version`, `profile_ref`, `stages`, `gap_id`, `target_reader_id`, `native_field`, `native_unit`, `basis_schema_id`, `allowed_source_kinds`, `effect_adapter_id`, `source_ledger_version` and HH-05 qualification IDs. Every reference resolves through an explicit server registry. Profile load rejects unknown readers/fields, unsupported currencies/bases, derived values, missing effect admission or missing tests. Do not infer safe editability from a numeric field or general JSON schema.

First slice has two delivery levels. Research-only proposals can be supported before field application by reading an exact owned target and showing unavailable application with a reason. Applied proposals require the exact native effect adapter and HH-01 coverage; do not falsely label a card path atomically guarded when that adapter is absent. Register these candidates individually:

| Gap | Intended native target | First-slice treatment |
| --- | --- | --- |
| `eh_site.tariff_level` | The native tariff row/setting selected by existing tariff services | Source proposals and ranges; apply only after the concrete tariff reader, unit/basis and preview/apply adapter are qualified. No guessed scalar `Generator.marginal_cost` substitution. |
| `eh_site.genset_fuel_price` | Exact target used by native fuel-price input handling | Source proposals; apply only with fuel/energy basis, conversion semantics and atomic field adapter established. Do not reinterpret fuel EUR/litre as electricity EUR/MWh. |
| Decision-study numeric assumption | Existing ledger row accepted by `apply_user_row` | Only a row whose exact schema/unit and existing manual effect path are qualified; unsupported row types remain proposal-only. |
| Grid-code limits, fault level, regulatory claims | Existing GUI-owned publish/configuration path | References for owner review only; never research-auto-publish or envelope-admit. |
| Arbitrary CAPEX/finance/dynamic-control parameters | No blanket adapter | Unsupported until a named profile amendment declares real reader, money year, effect and tests. |

Activation requires a concrete source-code handoff naming actual qualified target readers and fields, not just these conceptual labels. Initial slice exit requires native full gate coverage for the approved first-slice question; a missing adapter or unresolved acceptance item leaves it incomplete. Generic optional readers cannot convert one supported scalar into all-domain support.

## Research-stage request and offered subset

Consume HH-06 `StageRequest` rather than define a competing task protocol. Add a typed research payload: `gap_adapter_ref`, `intent_ref={intent_id,intent_revision,intent_sha256}`, `acceptance_ids`, exact target observation/reference, required native unit/basis, current value provenance, allowed source kinds, previously inspected source/proposal refs and HH-03 budget scope. Package instructions stay in `harness/workflows/profiles/`; changing content stays logged per-turn data.

Research is allowed only at input stage 2 for registered gaps and explore stage 6 for approved sourced ranges. It never runs silently during a correction round or ordinary chat. If the owner asks for a new research question mid-cycle, pause and prepare the separately scoped research stage; reruns await its outcome. Stage 6 follows the existing at-most-four case cap; a source range cannot create unlimited exploratory solves.

The exact offer is registered read-only native tools plus provider-neutral search/fetch capability and one source-ledger proposal operation. No `update_component`, tariff application, ledger application, solve, shell, external MCP or arbitrary network client is offered. Source-ledger proposal writes append research metadata only, never project inputs. It is not a general catalogue write tool. Validate actual provider offers and dispatch membership, not merely prompt instructions. A malicious fetched page cannot expand tools or invoke another stage.

New typed outputs are `ResearchFindings`, `ParameterProposal` and `ResearchUnavailable`, each with stage/request identity. `ResearchFindings` lists source refs, conflicts, missing basis and applicability limitations. `ParameterProposal` references exact verified source rows, a candidate scalar/range, native target unit/basis, assumptions, short reason and unresolved conflicts. `ResearchUnavailable` gives capability/budget/source reasons. None contains `approved`, authoritative confidence, native pass or a write instruction. The server validates all outputs and records raw malformed output as rejected, charging actual calls.

If a provider lacks supported search/fetch, return `not_available`; keep existing user/default inputs explicitly labelled and list the gap. Never silently replace a missing search tool with fabricated links or a model-memory value. A user can supply a document as an explicitly uploaded source with its own integrity/provenance rules; it does not become a fetched URL source automatically.

## Verified source ledger and schema

Extend existing study-harness `SourceRow` to version 1 typed records in the task-owned research artifacts. Reuse HH-06 history identity and owned storage transactions. Models submit source proposals; only server retrieval/verifier code writes verification fields. Keep append-only revisions so later fetches cannot rewrite a quote used by an earlier result.

```text
FetchedSource v1 = {
  source_id, revision, stage_request_id, provider_attempt_ref,
  kind: fetched_web|uploaded_document,
  requested_url?, resolved_url?, title, publisher?, author?,
  published_at?, document_version?, effective_period?, fetched_at,
  content_sha256, retained_content_ref, locator_kind, retrieval_status
}
SourceRow v1 = {
  row_id, revision, gap_adapter_ref,
  intent_ref: {intent_id, intent_revision, intent_sha256}, acceptance_ids, target_ref,
  source_ref, quote, quote_locator, quote_match_status,
  value, value_type: scalar|range, unit, basis,
  applicability, uncertainty_reasons, conflict_group?,
  verification: verified|unverified|unavailable,
  status: proposed|confirmed|rejected|superseded,
  proposal_ref?, confirmation_ref?, applied_receipt_ref?
}
```

`basis` is a gap-adapter-specific closed object, including physical commodity, input/output basis, location/market, time coverage, technology scale and, for money, currency, price year, nominal/real basis and tax/inclusion assumptions. Missing fields remain explicit unknowns; do not populate them from title, current date or locale. Distinguish document publication date, effective tariff period, fetched date and monetary price year. A current page can quote a historical cost.

For web records the requested URL must come from an actual admitted search/fetch in that research request and the resolved URL/redirect chain from retrieval receipts. A search snippet alone is a snippet source, not proof of a full document quotation. Verify quote text verbatim against the retained decoded content with the recorded extraction version and character/page/table locator. Deterministic line-ending/Unicode normalization, if used, is recorded with original bytes and never changes numbers, signs or units. Retain content digest and bounded excerpt needed for audit; use existing spill limits for large content and refuse unsupported extraction rather than make up a quote. Quote length remains the existing at-most-400 characters. Matching a quote does not prove its numeric interpretation: store the quoted numeric/unit evidence and verify the structured value against the registered extraction/interpretation contract, otherwise mark it unverified for application.

Do not fetch an arbitrary model-supplied URL merely to retroactively legitimize its citation. An unverified URL/quote may be displayed as an unverified lead, with no “sourced” badge and no eligible researched-value application. Broken, unavailable or access-restricted content stays unavailable; no invented DOI, publisher, page, date or URL. Retrieved instructions are fenced data and cannot alter confirmation, privacy, tool policy or budgets.

Ranges carry both endpoints, exact units and stated basis; the assistant must distinguish a source's range from its own synthesis or recommended scenario. A synthesized range lists every contributing source and method/limitations; it never masquerades as a direct quote. Contradictory sources remain separate records with a conflict group and reasons such as region, date, scale or unknown basis. No hidden average, newest-wins rule or cherry-picked low endpoint is introduced. If the sources cannot be reconciled, the owner sees the conflict and may explicitly select a labelled assumption or defer; no default resolution from model self-confidence.

## Approval, native effect and audit lifecycle

Researching, choosing and applying are distinct durable events. The source card shows candidate value/range, exact native target and unit, old value, currency/price year, quote/link/version, uncertainty/conflicts and expected affected study stages. A range is never written to a scalar field: the owner chooses an exact scalar or explicitly approves an existing bounded scenario set. Recommendation text must not conceal the range or conflicting evidence.

The source selection prepares an ordinary old→new edit card, preserving server-owned provenance even if the downstream tool arguments have no `research_*` field. A source-derived value always requires explicit user confirmation regardless of envelope, magnitude, prior approval of a similar value or whether the value equals an existing default. Approval binds source revisions/digests, proposal revision, exact target/InputVersion, units/basis, requirements revision/digest and preview digest. `approvals.asked/decided` is the durable audit receipt. A Choice click can select a proposal; it cannot stand in for native edit confirmation.

Before applying, fresh ACL/restrictions, source integrity, current target, schema and requirements are rechecked, then HH-01 validates the actual native candidate and captures effect/readback inside the effect boundary. If the target field lacks coverage, application pauses unsupported. Unit mismatch refuses: v1 adds no implicit currency, money-year, annualization, percent or energy conversion. A separately qualified native conversion must show inputs/formula/bases and require a new user card; no model arithmetic can supply authority. Changing a price year without an actual conversion never turns historical money into current money.

Ledger `confirmed` means the user selected that research assumption; native `applied_receipt_ref` separately establishes whether the field was actually changed. A failed/uncertain write does not become applied because confirmation succeeded. Rejected rows remain rejected; superseded source/proposal revisions stay inspectable. On success record the exact native receipt, then invalidate affected readiness/results through existing native behavior. Continue HH-06 only after fresh gates; every automatic initial solve/rerun consumes [HH-10](10-immutable-solve-admission.md) immutable attempt/job/input/evidence tuple. No forced shared mutable physical network or unsupported engine worker is authorized.

Complete audit includes request/acceptance identity, query and actual offer, source retrieval receipt, content hash/excerpt/version, proposed value/unit/basis, conflict and uncertainty notes, user decision, edit-card receipt, native input before/after, resulting exact jobs/results and budget reservations/settlements. Store short justifications, never hidden model reasoning or credentials. HH-09 DecisionPacket preserves unresolved research and human-review acceptance items; HH-08 exports the same source/proposal revisions with the same acceptance IDs. Reuse coverage states `pending|running|software_passed|needs_user|unsupported|incomplete|failed|accepted|superseded`: source verification can pass only its exact software checker, and user assumption approval closes only its designated decision item. The broader study requires all prescribed checks and decisions. A report claim cannot upgrade “user-approved assumption” to “independently verified parameter.”

## Budgets, background work and recovery

Use existing `research.cost_cap_eur`: EUR2 default, user raisable up to EUR10 hard cap. Enabling research does not raise the overall run budget. Each search, fetch and inference physical send needs HH-03 `PreparedCall`/`CallBounds`, purpose attribution and durable reservation before send, including fixed search charges where applicable. A provider offering search without a conservative bound or complete supported usage categories is unavailable for strict monetary admission. Token-only metering cannot certify the search cap. Unknown usage retains the reservation and stops further covered calls.

HH-03 currently forbids implicit FX; honor that canonical rule. A provider billed in another currency is unsupported for strict EUR research until a separately reviewed pricing adapter supplies the explicit source/rate/date/bound contract required by study design. Do not smuggle FX into this ticket or compare USD floats to EUR. Provider-rate facts are not researched model-input assumptions and do not belong to this source proposal workflow.

Research runs on the existing server runner under explicit owner/project context while the owner uses other activities or closes the browser. It shares the original run wall deadline, not a browser timer. Waiting for approval does not pause expiry. Only independent stages that do not read the unresolved values may continue, with dependency IDs checked natively; stages needing them stay `needs_user/research_confirm`.

Persist request/reservation and retrieval identities before sends. Recovery follows matching durable receipts; it cannot automatically resend an unknown charged request. Search results returned late append once under the original attempt. Resume revalidates access, source integrity/expiry if an adapter declares one, target drift, profile/requirements and remaining budget. A source's changed remote page cannot overwrite a retained prior source revision. Missing retained content requires fresh retrieval/new revision and review, not silent refresh under old approval. Pause/stop revokes live application cards and prevents new research admissions; completed research and rejected proposals remain history. Browser reconnect cannot recreate approval or clear unknown spend.

## Minimal API/UI, files and migration

Extend existing run view and `research_confirm` waiting projection; do not add a research dashboard. The run card shows “Researching …”, “Sources need review”, “Waiting for your value approval”, unavailable/conflicting sources and explicit “Assumption approved” versus “Applied” receipts. Source detail expands within existing cards. Follow existing return/navigation behavior; no automatic panel switch or research-value notification email. The old→new card remains the single native effect approval.

New bounded request objects for source selection/rejection use inspected research/proposal revision and source digests through existing run/stream action plumbing in `routers/chat.py`; they prepare cards only. Actual application follows existing confirmation endpoint, never a new direct setter. Reads recheck project ACL and cannot trigger fetch. Public payloads contain owned IDs and display links, never internal paths or bearer grants. Add typed conflict/unavailable errors with existing error fragments and closed frames only where required.

Existing files: `harness/protocol.py` for neutral capability vocabulary, `harness/providers/*` for actual search/fetch translation, `harness/runner.py`, `services/run_profiles.py`, `run_envelope.py`, `assistant_tasks.py`, `run_monitor.py`, `harness/edit_card.py`/private records only to bind provenance, `routers/chat.py`, `frontend/src/api/runMonitor.ts`, `components/runMonitor/RunMonitor.tsx` and existing edit-card display. Existing `services/study/ledger.py` is reused only for qualified decision rows. New files: `harness/research.py` for typed requests/verification/transitions, `harness/research_sources.py` for owned source artifacts, `tests/test_harness_research.py`, `tests/test_harness_research_recovery.py`, offline source fixtures, profile research Markdown, one release and typed-error fragment. Reuse HH-06 history and HH-03 accounting instead of duplicate ledgers. No new general catalogue browser tools, native formulas or checked profile changes.

Migration adds versioned research metadata to eligible version-3 run artifacts. Legacy unversioned SourceRows remain historical/unverified and must not receive fabricated verification/approval receipts. Existing currency fields remain EUR; malformed USD-only legacy proposals require re-review. Profile research stays off until qualification. Rollback disables new research stages and card preparation, preserves all rows/receipts/reservations and prevents pending cards using downgraded legacy apply paths. Existing native manual input editing remains available under its own policy.

## Required tests and integrated user journeys

All tests are offline with retained synthetic content, fake provider transports and native target/effect readback. Independent expected values and source-byte fixtures must not call the verifier under test to generate their own oracle. Required IDs extend HH-05 without replacing existing mandatory cases.

| ID | Case | Required result |
| --- | --- | --- |
| R01 | Supported registered gap and provider | Useful proposal with exact verified quote/unit/basis. |
| R02 | Ordinary chat or correction-round research | Search/fetch absent and dispatch refused. |
| R03 | Research provider requests write/solve | Not offered, no native effect. |
| R04 | Missing provider search capability | `not_available`, existing assumptions labelled, no invented link. |
| R05 | Fabricated URL or fabricated quote | Unverified source; no sourced badge/application. |
| R06 | Exact quote but altered number/unit | Numeric interpretation rejected or unverified. |
| R07 | Prompt injection in fetched content | Offer, policy, thresholds and confirmation unchanged. |
| R08 | Price-year/currency or commodity mismatch | Explicit basis refusal, no implicit conversion. |
| R09 | Conflicting sources and scalar target | Both shown; exact owner selection required. |
| R10 | Live envelope and in-bound researched value | Fresh explicit card in Guided and Expert. |
| R11 | Downstream args omit research markers | Server provenance still prevents envelope admission. |
| R12 | Rejected/superseded proposal | Retained history; no replayed application. |
| R13 | Target/source/requirements change after preview | Stale refusal, no effect or recreated approval. |
| R14 | Missing exact field/HH-01 adapter | Proposal inspectable; application unsupported. |
| R15 | Research cap/default/hard-cap validation | EUR2/EUR10 semantics; send reserved before cap crossing. |
| R16 | Search charge missing or unknown send usage | Strict admission unavailable/hold retained; no retry. |
| R17 | Worker/browser restart during retrieval | Matching attempt recovered once; no duplicate charge. |
| R18 | User approval then crash during application | Native receipt/readback or uncertain; no duplicate edit. |
| R19 | Late result after stop | History appended once; no card/application/solve continuation. |
| R20 | Foreign source/task ID or corrupt content | Non-leaking ACL/unavailable; no latest-source fallback. |
| R21 | Ledger row confirmed but write fails | Confirmed assumption distinct from applied receipt. |
| R22 | Unresolved acceptance item exported | Same HH-09 ID and honest incomplete HH-08 closure. |
| R23 | Legacy source/schema and rollback | No fabricated verification; pending authority cannot downgrade. |
| R24 | Result objective improves after price update | Changed assumption shown; no physical/scientific certification. |

J07-A requests a tariff assumption, researches two conflicting dated sources, reviews quotes and units, selects one scalar, explicitly approves its old→new card and follows HH-06 through fresh native checks and HH-10 solve into HH-08 report. If native tariff admission or gate coverage is unavailable, expected outcome is proposal-only with the original acceptance item incomplete. J07-B researches a decision-study range, rejects one value and approves a supported row; rejected and approved revisions survive restart and export with exact receipts. J07-C closes the browser during search, works on another project, then returns to a pending card after worker recovery; no duplicate retrieval or approval occurs. J07-D injects a convincing fabricated citation plus an instruction to weaken a failed rule: the source remains unverified and the physical failure remains visible. Execute each supported provider/mode variant and preserve unsupported capability results distinctly from useful completed research.

Handoff requires exact adapter registry entries, source fixtures, observed offers, native receipts, budget/unknown-spend cases, migration/rollback outcomes and UI screenshots in both modes. Skipped: external scholarly tools, live searches, paid calls, application implementation, PR and contact with reviewers. Remaining risk: source integrity and user approval do not validate real-world applicability; unsupported native fields, provider bounds and scientific checks must remain explicitly incomplete until separately qualified.
