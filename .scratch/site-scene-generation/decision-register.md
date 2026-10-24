<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Contract decision register

Status: all decisions below are unresolved implementation gates; architecture rules already specified remain binding

These are questions requiring source inspection/prototype evidence, not invitations to redesign the agreed workflow. The implementation agent proposes a closure; the applicable owner/coordinator/reviewer accepts it through existing rules. No acceptance or reviewer contact is implied here. Populate the decision record before the blocked unit starts; link exact records from its PR.

| ID | Freeze/prove | Evidence and boundary | Must close before |
|---|---|---|---|
| D01 | Native confirmed site anchor DTO/persistence/ACL | Exact owning setting/service/route/schema, valid confirmed 0/0 versus placeholder, empty-network and multi-site lookup, fixed origin versus anchor, revocation and location-change behavior. No dummy Bus. | SC-00 N acceptance / SC-01 |
| D02 | Public API/flow/tool contract | Current OpenAPI comparison; exact method/path/request/response/status/error kind, role/restriction/lock matrix; version/adoption semantics; every route's chat disposition; scene/model/display intents and confirmations. Use contracts.md proposed names unless a reviewed source-grounded amendment is recorded. | SC-00 N acceptance / SC-01 and SC-07 foundation |
| D03 | Generation/CAS and copy lifecycle | Concrete storage/lock/atomic pointer/receipt implementation; crash injection before/after commit, stale legacy writer, one-generation snapshot/export, closure-only scenario/Save-As refs. Record actual platform guarantees and cleanup rules. No independent legacy dual-writer. | SC-00 N acceptance / SC-01 |
| D04 | Native proposal execution and limits | Choose bounded native execution versus typed runner based on measurement; demonstrate no solve fallthrough. Freeze queue/runtime/memory/concurrency/cancellation limits and named error behavior for the native subset, including tests on both sides of limits. | SC-00 N acceptance / SC-01 |
| D05 | Private-media ownership/retention | Native org/Project/media identities including flat projects, exact private storage and ACL, operator-visible retention/quotas, copy/export/restore/delete/expiry rules and marked-byte leakage probes. Upload disabled until its actual policy/limits are enforced. | SC-00 N policy acceptance; SC-06 upload enablement requires runtime proof |
| D06 | Native target lifecycle identity | Trace real rename/delete/restore/copy/same-name-recreate hooks; prove no stale binding aliases a new component. Record identity generation/copy mapping or reviewed scope amendment. A label or Class:name alone is insufficient. | SC-02 / SC-07 bindings |
| D07 | Native result quantity registry | Exact native selectors/ports/signs/units/conversion factors/time and weight semantics, freshness/binding checks, supported group boundary rule and unavailable shapes. No hourly throughput inferred from summary. | SC-03 / SC-07 results |
| D08 | Native source/fixture/license policy | Synthetic fixture ownership, actual source versions/terms and permitted derived redistribution, explicit coordinate/unit/height evidence classes and calibration. Reopen only affected source rows for new optional data. | SC-00 N acceptance; each new source before ingestion |
| D09 | Blender adapter/worker | Pinned tested Blender/version/OS, trusted scripts, manifest/ID round trip, worker sandbox/resource/cancel behavior, no arbitrary blend execution. Real scale/binding evidence, independent of Native status. | SC-00 B acceptance / SC-05 and advertised Blender compatibility |
| D10 | Photo adapter/registration | One supported selected stack, license/hardware/runtime packaging, actual reconstruction/texturing and scale/control residuals, failure criteria, own measured resource gates. Sparse/unregistered output stays reference-only. | SC-00 P acceptance / SC-06 photo enablement |
| D11 | Assistant bounded context and offers | Finite per-turn fields/token/tool budgets, current owner/project/site/revision/intent scope, schema/tool/safety/approval mapping and exact receipts; regression of bounded provider tool offers, no full mesh/media injection. Scripted real-native journey plus denial/unknown effect. | SC-00 N acceptance / SC-07 foundation |
| D12 | Target-device native performance gates | Pinned browser/device/packaged app, reference scene size, frame/latency/memory measurements and supported limits; native manual fallback with optional engines absent. Optional engine thresholds recorded separately with D09/D10. | SC-00 N measured budget; native release needs real device confirmation |

## Decision closure record

Copy one record per decision into the implementation PR/evidence directory; update this table's status only with an accepting reference.

- Decision ID / status: unresolved, proposed, accepted, blocked or superseded.
- Exact source base, spec revision, owning paths/symbols and contract version.
- Existing behavior observed; candidate alternatives and why one is selected.
- Frozen DTO/error/units/limits examples; affected routes/tools/test cases.
- Positive, negative, boundary/race fixtures and independent expected results.
- Exact commands, environment/engine version, fixture hashes, exit status and evidence URLs.
- Unavailable evidence and effects on downstream units; no implicit pass.
- Accepting review/owner reference and full reviewed head; superseded decision and migration impact if applicable.

When later source changes invalidate a closed decision, mark affected dependents blocked and revalidate. Do not reuse an accepted decision from an incompatible master/contract without examining the change. The immutable-generation authority, private originals, native connectivity, separate scene/model effects, exact unknown values and existing flow/companion are already normative; they are not open alternatives in this register.
