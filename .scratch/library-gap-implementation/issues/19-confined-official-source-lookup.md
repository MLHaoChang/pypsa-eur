<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-19: Audit and close only missing official-source lookup controls

Status: needs-triage
Type: research-and-implementation
Priority: P2
Group: Shared
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Owner-approved live lookup design exists, but collecting data must not bypass current fetch confinement, approvals, terms, budgets or read-only preview safety.

## Research assignment

Which L1-L6 library/live-lookup requirements are now implemented, and is a minimal missing bounded fetch/parser slice still necessary?

Candidate starting points, not import approval:

- Existing .scratch/project-workflow/library-and-live-lookup.md sections 3.1-3.7 and owner decisions Q13-Q16; current harness/native fetch routes and tests discovered in audit.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `.scratch/project-workflow/library-and-live-lookup.md`
- `pypsa-gui/backend/harness/README.md`
- `pypsa-gui/backend/services/library`
- `pypsa-gui/backend/routers/library.py`
- `pypsa-gui/backend/harness/catalogue.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Build a current requirement-to-code/test map before any code. Reuse completed L1-L6 work and outsourced-task results; identify the smallest remaining fetch or pure parser gap, file scope and runtime enablement policy.
2. Only on an explicit reviewed plan, implement the missing native controls: fixed official host/path selectors, HTTPS/redirect/DNS/IP enforcement, GET/no credentials, content/byte/depth/time caps, per-turn/chat/org/host budgets, admin opt-in/kill switch and audit. Parser is pure bytes-to-native-schema plus per-field spans; content is fenced untrusted data.
3. Use a local fixture server and deterministic scripted chat. A successful fetch yields only a source-labelled proposal, then ordinary confirmation and pinned native apply. Keep disabled/offline fallback and no automatic common promotion. Dedicated security review is required before a new live fetch capability merges.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-19-01 | Off-list host, lookalike IDN, redirect escape, private/metadata IP or DNS rebinding. | Connection/redirect is refused at the enforced boundary and audited. |
| LG-19-02 | Oversize/unsupported content, timeout, exhausted budget or too many attachments. | Bounded failure; no uncontrolled retry, credentials or external request chain. |
| LG-19-03 | Page says to change costs, fetch another host or call a write tool. | It remains data; no extra authority, write or unapproved fetch. |
| LG-19-04 | Admin has not enabled lookup or kill switch is active. | No network call and existing offline/library path remains usable. |
| LG-19-05 | A permitted document parses to a valid proposal but source rights are user-scoped. | Only reviewed org/local use; no common promotion, silent apply or evidence inflation. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `Existing confined-fetch/parser/security suites identified in audit; exact path scope required before implementation`
- `test_chat_tools_library.py`
- `test_library_reference_apply.py`
- `test_write_surface_lock_policy.py`

## Stop conditions and handoff

Desk audit first; do not start a new crawler or duplicate L4. Actual live enablement, security/domain review and publisher contact are not authorized by this specification.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
