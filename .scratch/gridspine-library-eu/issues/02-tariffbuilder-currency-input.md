# 02. The tariff builder has no currency input

Status: in-review
Type: task
Raised by: GK B (27), plan verdict on issue 01 (condition C4), 2026-11-06.
Gated: no (UI; UX review).

## What is missing

Since issue 01, a Library tariff needs a currency, stated or implied by an ISO jurisdiction. `TariffBuilder.tsx` has no currency field: its jurisdiction is free text (`TariffBuilder.tsx:352`), so a user who types `generic` or an unknown code is refused with `tariff_currency_required` (422, shown beside the jurisdiction). The only fix they have is to type a country code. A tariff in a currency other than its country's (an NL tariff priced in USD) cannot be built at all.

## What a fix would need

- A currency input (ISO 4217) in the builder, defaulted from the jurisdiction, sent as `payload.currency`.
- The `tariff_currency_required` field error shown on that input too.
- With the input, the `tariff_currency_required` message gets back its second half ("or state the currency"); UX (10) dropped it from issue 01's text because the builder had nothing to state it in.
- The builder's field-error list shows the raw key ("jurisdiction: …"); UX (10) asks for the form's label word instead (`TariffBuilder.tsx`, the list beside `Errors`).
- UX review of where the input sits in the workflow.

## Pointers

- `pypsa-gui/frontend/src/pages/results/investment/TariffBuilder.tsx`, `tariffModel.ts` (`fieldKey`).
- `pypsa-gui/backend/services/library/items.py` (`TariffCurrencyRequired`), `routers/library.py` (`_currency_required`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pages/results/investment/TariffBuilder.tsx`, `tariffModel.ts`, `services/library/items.py`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12) (not gated otherwise).
- Size: M.
- Currency input defaulted from jurisdiction; restore "or state the currency" in the message; field labels in the error list. Needs UX placement OK in its plan.
