# 04. The DC energy-price check trusts only a stated tariff currency

Status: ready-for-agent
Type: task
Raised by: GLE-03 session (issue 03, PR #1279), GK A follow-up, 2026-11-08.
Gated: yes (money semantics).

## What is missing

Issue 03 made the currency gate (`services/commercial/binding.tariff_currency_mismatch`, used by preflight and attach) read an unstated currency from a pack copy's `pack_hash` stamp (`defaults_pack.loader.stamped_tariff_currency`). `services/dc_architecture.tariff_energy_rate` checks the tariff currency itself and reads only `tariff.currency`. A copied `generic` pack tariff (`tou_reference_illustrative`, EUR in its pack meta) in a GBP finance case passes it and is read "as the finance currency (assumed)".

## What a fix would need

- `tariff_energy_rate` reads the same currency the gate does: the stated currency, else `stamped_tariff_currency`. It refuses `currency_mismatch` on a stamped generic copy in a non-EUR case.
- A test: a GBP finance case with the copied generic tariff is refused; an EUR case is not. Copies keep their hashes, since nothing is written.

## Pointers

- `pypsa-gui/backend/services/dc_architecture.py` (`tariff_energy_rate`, the `cur = getattr(tariff, "currency", None)` check).
- `pypsa-gui/backend/services/library/defaults_pack/loader.py` (`stamped_tariff_currency`).

## Comments
