# 05. `tariff_payload_resolved` does not show a stamped tariff's currency

Status: needs-triage
Type: task
Raised by: GLE-03 session (issue 03), repeated by GLE-04 (issue 04, PR #1290).
Gated: yes (money semantics).

## What is missing

`models.commercial.tariff_payload_resolved` shows a tariff's stated currency only. A copied `generic` pack tariff states none, so its resolved payload shows no currency even though the currency gate and the DC energy-price check now read EUR from its `pack_hash` stamp (`binding.resolved_tariff_currency`).

## What a fix would need

- The resolved payload shows the resolved currency and says it came from the stamp, using `resolved_tariff_currency`. Nothing is written onto the tariff, so hashes stay as they are.
- A test: the copied generic tariff's resolved payload shows EUR from the stamp; a tariff that states a currency shows that one.

## Pointers

- `pypsa-gui/backend/models/commercial.py` (`tariff_payload_resolved`).
- `pypsa-gui/backend/services/commercial/binding.py` (`resolved_tariff_currency`).
