# 01. A tariff has no currency, so an EUR tariff attaches to a non-EUR project unchecked

Status: done
Type: task
Raised by: the gatekeeper on #521 (P2d, EU-C09 TenneT 2026), 2026-10-21; recorded under the owner's usage pause as a ticket, not work.
Gated: yes (money semantics; schema change on `models.commercial.Tariff`).

## What is missing

`models.commercial.Tariff` carries no currency. The shipped common TenneT tariffs (`services/library/common_items/tariff/nl-tennet-2026-*.json`) state EUR only in their meta. Once regional packs in another currency exist (#499: CA/UK/US), a project priced in GBP or USD can attach an EUR tariff, and the bill adds EUR charges to non-EUR costs with no warning.

## What a fix would need

- A `currency` on `Tariff` (ISO 4217, default: what? decide; existing org tariffs and URDB imports are USD/EUR by jurisdiction), with the canonical hash rule stating whether it enters the hash (a model change must not re-hash shipped Library items silently).
- A refusal (`currency_mismatch`, the #499 rule) when a tariff's currency is not the project's, at attach (`attach_tariff`, `PUT /simulation/solver_config` import_tariff_ref) and in preflight for an inline tariff.
- Tests: a GBP-priced project refuses the TenneT tariff; an EUR project attaches it; existing pins keep resolving.

## Pointers

- `pypsa-gui/backend/models/commercial.py` (`Tariff`), `services/commercial/preflight.py`, `services/chat_tools.py` (`attach_tariff`), `routers/library.py`.
- The apply-side currency gate for cost references: `services/library/reference_apply.py` (`currency_mismatch` on the recorded provenance currency).

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage`. Owner question Q-GS-1 (GK B, money semantics). Two choices are open in the ticket: the default currency for existing tariffs, and whether it enters the canonical hash. Proposed: (1, Recommended) `currency` is required on new tariffs, existing org tariffs and URDB imports get it from their jurisdiction on read, and it stays OUT of the canonical hash so shipped Library pins keep resolving; (2) required and IN the hash: cleaner identity, but every shipped tariff re-hashes and pins must migrate; (3) optional with default EUR: smallest change, but a USD tariff without the field silently reads as EUR (the bug itself). #499 (regional packs) is merged, so the gap is live. Once answered: ready-for-agent, Opus, files `models/commercial.py`, `services/commercial/preflight.py`, `services/chat_tools.py` (`attach_tariff`), `routers/library.py`; reviewer GK B.

2026-11-05, owner answer Q-GS-1 (coordinator card): "Required, outside hash (Recommended)". New tariffs must name a currency, existing tariffs take it from their jurisdiction, and the currency stays out of the canonical hash. GK B reviews.

### 2026-11-06: GK B (27) plan verdict, GO WITH CHANGES

Plan (GS-01 session): `Tariff.currency` (ISO 4217), unstated → the jurisdiction's; an implied currency stays out of every hash; a Library write refuses a tariff whose currency stays unknown; `currency_mismatch` at the solver-config PUT (and so `attach_tariff`) and in preflight.

- Point 2 (a currency that differs from the jurisdiction's enters the hash): GO.
- C1. The implied currency is frozen: pin the jurisdiction map with a test; Bulgaria explicit (BGN before 2026-01-01); a map-entry change is a hash-recipe change, never an in-place edit.
- C2. Drop the implied currency only where the hash and storage dump; API reads (a Library item GET, the tariff on the commercial config) carry the resolved currency, tested.
- C3. Byte-identity for everything stored or shipped: every pack tariff (list any whose meta currency differs from the jurisdiction's), org Library and URDB fixtures, a stored pin, `_network_input_fingerprint` and the investment-case digest of a project with an inline tariff; before == after.
- Point 3 ("required" = stated or implied): GO for this slice. C4: a typed 422 naming the missing currency, its words sent to UX (10); a follow-up issue for a TariffBuilder currency input.
- C5. A finance-currency change away from an attached tariff is caught (preflight covers the ref-resolved tariff too); both orders tested.
- C6. No shipped template or guided study trips `commercial.currency_mismatch`.
- C7. The typed refusal is tested where it is caught (`attach_tariff` kind; a mutant dropping the gate is red; a refused PUT leaves the solver config and network revision unchanged).
- C8. On a trial merge: `test_tool_error_kind_manifest.py`, the route ACL and write-lock policy tests.
