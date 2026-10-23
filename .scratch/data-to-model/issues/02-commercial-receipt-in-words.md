# The section 7 card and receipt name tariff and PPA values in words, with the currency

Status: done (#644)

## What

The commercial edit card's `says` text, which the chat and the dialog (PR-C #599) share, uses raw
paths and a unit with no currency, for example:

> Import tariff › energy periods[peak].rate 0.2 → 0.0725 /kWh

It should read, for example:

> Import tariff › Energy, peak period: rate 0.2 → 0.0725 EUR/kWh

(UX reviewer, #599 review, 2026-10-22, non-blocking.)

## To do

- In `services/document_values_commercial.py`, give `card_rows` a label in words for each tariff
  path:
  - `periods[<name>].rate` → "<Item>, <name> period: rate";
  - `periods[<name>].tier_rates[<j>]` → "<Item>, <name> period: tier j+1 rate";
  - `tiers[<j>].rate` → "<Item>: tier j+1 rate".
  The `says` text should use these labels.
- Show the card's money units with the stated currency ("EUR/kWh", "EUR/MWh"). Never show a
  field id, including PPA fields such as `premium_eur_per_mwh`.
- The card digest binds the rows, so update the CDV tests that pin `says` or the row labels.
- The chat path gets the same wording, so check `harness/edit_card.py`'s section-7 rows.
