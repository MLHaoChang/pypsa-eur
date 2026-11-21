<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Template journeys, step 2

## Step 2 result: the journeys (pypsa-gui/frontend/scripts/template-journeys*.mjs)

- Run: `PYPSA_COMPOSER_PYTHON=<python> node scripts/template-journeys.mjs --mode short|full [--template id,id] [--out dir]` after `npm run build`. One isolated backend (`composer-fixture.py --no-model`), one browser context, templates one after another. The backend storage lives outside `--out`, so uploaded artifacts hold no credentials.
- Expected-fail ledger: `gap(ctx, id, what, predicate)`. An open gap passes as xfail; a fixed gap fails the run ("remove its expected-fail marker"). Each predicate is what the fixed app must satisfy, so the builder who fixes gap NN flips its step in the same PR. Gap 01's predicate assumes "blocks Run"; if Q1 lands as "optional", change the predicate.
- New gap id `budget` (no issue file yet): Guided shows "Studying… up to N calculation steps" but offers no way to choose the size; only the Expert reference-design panel has the `budget_solves` field (1 to 120). See the PR note.
- The Escape pitfall and the sr-only radios from the earlier notes are handled in the library (`ensurePanel`, `site-type-*` click). A drawer left open covers the rail: `resetOverlays` reloads the page.
- Not a gap but a fact the journeys encode: `eh_ipp_hybrid` offers no run ("Not offered for this site: Nothing to make adequate"), so its journey ends at readiness with Run disabled and the reason named.
