# One money notation: `kpiMoney` and `money()`

Status: needs-triage
Type: task
Spec: UX-RULES-1107 ("the one money notation", `pypsa-gui/frontend/src/utils/money.ts`); UX audit M8
(`kpiMoney`, `pypsa-gui/frontend/src/pages/hubDesign/plainWords.ts`).

Found while building 02b-2 (UX 2 (3), 2026-11-18): the 02b GO assumed `kpiMoney` writes the same strings as
`money()`. It does not. Guided's tiles and their sentences use `kpiMoney`; Expert, the report and `money()` use the
other notation. Three differences:

1. **Space after the symbol.** `money()` puts a no-break space after the symbol ("€ 1.23 k"); `kpiMoney` puts none
   ("€1.23 k").
2. **Significant figures.** `money()` writes millions to two significant figures ("€ 7.9 m"); `kpiMoney` to three
   ("€7.89 M").
3. **The million mark.** `money()` writes "m"; `kpiMoney` writes "M".

`plainWords.test.ts` pins the `kpiMoney` forms (`€657 k`, `€7.89 M`).

## What to decide

Which notation Guided's tiles and sentences use, so a person reads one form of money across Guided, Expert and the
report. One section never mixes two forms: a sentence matches the tile above it (UX 2 (3)).

## Acceptance

- One notation for Guided and Expert, chosen by UX, with `kpiMoney` delegating to it or removed.
- A test asserts `kpiMoney(x) === money(x, 'EUR')` (or that `kpiMoney` is gone) for thousands and millions.
- The M8 pins in `plainWords.test.ts` and every Guided test that spells money follow the chosen form.

## Comments
