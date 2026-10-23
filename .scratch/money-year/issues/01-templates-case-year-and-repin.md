# Templates: move the case currency year and the pack pins to 2025 together

Status: needs-triage

Follow-up to the MONEY-YEAR-1 relabel (pack 2026-10-25, branch `claude/pack-money-year-relabel`).
The gatekeeper's plan review decided that the case year and the pins are one decision and move together.

## What

- Repin `TEMPLATE_PACK_VERSION` (`project_templates/eh_templates.py`) and `AMMONIA_PACK_VERSION`
  (`project_templates/eh_ammonia.py`) from 2026-10-22 to the relabelled pack. Check whether
  `REDUNDANCY_PACK_VERSION` (`services/adequacy/redundancy.py`) moves with them.
- In the same change, move the templates' case `currency_year` from the hard-coded 2020 to 2025
  (`eh_templates.py` x2, `eh_site.py`, `eh_staged_hubs.py` x2). Today the template assets are labelled 2020
  money, but the values are 2025 EUR.
- Check the money years that sit next to it. `eh_staged_hubs.CO2_PRICE` carries `money_year` 2020, and
  `results/baseline.py` inflates it to the base year. The seed tariffs (`tariffs.json`) and the finance defaults
  (`finance.yaml`) are 2020. Decide each one from its source, not by analogy.
- Regional packs (2026-10-21+*, 2026-10-22+*) take their EU base from 2026-10-22, so their `eu_fallback`
  rows still carry the 2020 label. A regional rebuild on the 2026-10-25 base would carry the fix.

## Why it was left out

Nothing converts a pack cost by its money year, so the relabel moved no result. Repinning without moving the
case year would show 2025 asset costs inside a case that says 2020.
