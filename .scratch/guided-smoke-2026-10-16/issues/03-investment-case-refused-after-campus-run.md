<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Investment case refused once a campus run has bought assets (money-year mismatch)

Status: ready-for-human
Type: bug (may be by design: confirm)
Found by: Guided smoke re-run, 2026-10-16, master dc4e4b37

## Error

`POST /api/results/investment_case` after a successful campus run (invest=true)
on `eh_microgrid_staged`. The study
record ends `status: refused`:

```
error_code: campus_assets_money_year_mismatch
error: "campus_assets_money_year_mismatch: the campus assets are not priced in the case's 2020 money"
stage: build_case
```

The same call before the campus run succeeds (NPV figures below).

## Where

- Raised: `routers/results.py:1654` (`CampusSource.refused("campus_assets_money_year_mismatch", problem)`),
  with the message built in `_ic_money_year_problem` at `routers/results.py:1637`
  (`year != fin.currency_year`: the library's `price_year` vs the template's
  finance `currency_year` 2020).

## Repro

1. Create `eh_microgrid_staged`, solve.
2. Campus draft + run (k=3, invest=true).
3. `POST /api/results/investment_case {}` -> poll `GET` -> `refused`.

## Notes

Guided's stage order (investment case first) hides this; a user who runs the
campus study first, or re-runs the case after it, gets a refusal. The ledgered
demo NPVs (pre-tax project NPV, `detail=full`) reproduce only on a project
with no campus study: data center -7,992,548; H2 hub +14,595,554;
microgrid +43,857,725.

## Comments

- 2026-10-16 (session_01TP6mmWWALLE8q4y73ypwoq): by design. The finance plan 2026-10-12 (ic-g3, R-3) refuses campus assets whose price year is not the case's currency year, and nothing converts with a price index. The shipped defaults always differ: gridspine's campus library is in 2026 money (`campus_assets.yaml` price_year) and every hub's finance inputs are in 2020 money. The owner chose to keep the refusal and have it say what to do (option A). `claude/guided-ic-money-year-message` names both years and the way out. The Investment tile shows the same text.

