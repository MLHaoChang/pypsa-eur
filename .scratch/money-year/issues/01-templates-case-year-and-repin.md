# Templates: move the case currency year and the pack pins to 2025 together

Status: needs-triage
Progress: the relabelled pack 2026-10-25 merged (#688, 719f8b01); the repin and the case-year move are not done.

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

- The guided study pin (`services/study/library.PACK_VERSION`, 2026-10-05) moves under the same rule. Pack
  2026-10-25 relabels four rows it reads (battery.power/energy.overnight, solar-utility/rooftop.investment.overnight)
  2020 -> 2025; the bump tripwire in `pypsa-gui/backend/tests/test_study_ledger_from_pack.py` acknowledges exactly
  that change (`_ACKNOWLEDGED_BUMP_CHANGES`). Bumping `PACK_VERSION` past 2026-10-24 alone hits `currency_year_mixed`
  against the 2020 tariffs (`services/study/compile.py` `currency_year_of`, which refuses a case whose money rows,
  tariff and study state different years), so move the study's or the tariffs' currency year together with the pin,
  and drop the acknowledgement in the same change.

## Why it was left out

Nothing converts a pack cost by its money year, so the relabel moved no result. Repinning without moving the
case year would show 2025 asset costs inside a case that says 2020.
