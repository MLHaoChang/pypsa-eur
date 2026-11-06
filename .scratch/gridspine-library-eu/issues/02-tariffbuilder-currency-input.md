# 02. The tariff builder has no currency input

Status: needs-triage
Type: task
Raised by: GK B (27), plan verdict on issue 01 (condition C4), 2026-11-06.
Gated: no (UI; UX review).

## What is missing

Since issue 01, a Library tariff needs a currency, stated or implied by an ISO jurisdiction. `TariffBuilder.tsx` has no currency field: its jurisdiction is free text (`TariffBuilder.tsx:352`), so a user who types `generic` or an unknown code is refused with `tariff_currency_required` (422, shown beside the jurisdiction). The only fix they have is to type a country code. A tariff in a currency other than its country's (an NL tariff priced in USD) cannot be built at all.

## What a fix would need

- A currency input (ISO 4217) in the builder, defaulted from the jurisdiction, sent as `payload.currency`.
- The `tariff_currency_required` field error shown on that input too.
- UX review of where the input sits in the workflow.

## Pointers

- `pypsa-gui/frontend/src/pages/results/investment/TariffBuilder.tsx`, `tariffModel.ts` (`fieldKey`).
- `pypsa-gui/backend/services/library/items.py` (`TariffCurrencyRequired`), `routers/library.py` (`_currency_required`).
