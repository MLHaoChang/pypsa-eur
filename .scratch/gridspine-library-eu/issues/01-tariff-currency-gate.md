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
