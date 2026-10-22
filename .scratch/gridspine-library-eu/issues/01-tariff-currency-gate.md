# 01. A tariff has no currency, so an EUR tariff attaches to a non-EUR project unchecked

Status: needs-triage
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
