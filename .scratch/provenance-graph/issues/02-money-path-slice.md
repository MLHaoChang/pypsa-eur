# Money path: the graph core, the asset card, the Economics table and the Guided ledger

Status: ready-for-agent
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B; the UI part also needs the UX reviewer's OK.
Blocked by: 01.
Absorbs: TCU-07 (`.scratch/typed-cost-units/issues/07-guided-ledger-does-not-show-out-of-date-cost.md`,
owner decision TCU07-TRIAGE-1). When this issue is done, TCU-07 reads `Status: done (#<this PR>)`.

## What to build

The graph core from the plan GO (types, builder, `staleness`, `why`), filled with the money path only:

- `input` nodes for asset fields stamped in `n.meta["asset_params"]` and for Guided ledger rows;
- `derived` nodes for priced costs (overnight cost, FOM) and the annualised cost on the Economics row;
- `priced_through` edges for the TCU-05 scale inputs (`dc_ac_ratio`, a Link's output efficiency);
- `stamped_with` edges from the provenance entries;
- `shown_in` edges to the asset card, its read-out, the Economics costs table and the Guided ledger row.

Then move these readers onto the graph (spec survey, "Money path"):

- `routers/network.py:802` and `:818` ask the graph instead of calling `provenance.staleness` directly;
- `services/study/asset_view.py` and `routers/studies.py:1074`: the Guided ledger row carries the same `stale`
  verdict as the asset card for the asset field it compiles to (`LEDGER_FIELDS`);
- `frontend/src/pages/decision/LedgerReview.tsx` shows the out-of-date note through `staleNote`;
- `frontend/src/components/ProvenanceChip.tsx`, `layout/properties/AssetFields.tsx` and
  `pages/economics/AssetCostsTable.tsx` keep their wording, read from the verdict.

`commercial.provenance` cells stay out (issue 08). A cost whose commercial input the graph does not model reads
`uncompared`, never fresh. Conditions P1–P3, P5 and P8 of the plan GO (issue 01, `## Comments`) apply.

## Acceptance

- Change a PV generator's DC/AC ratio after pricing: the asset card, the Economics costs table and the Guided ledger
  row all show the cost out of date, with the same note.
- An unrecorded scale input reads stale with reason "scale input not recorded" on all three (TCU-05 behaviour kept).
- No migrated reader keeps its own comparison; a test fails if one calls `provenance.staleness` directly.
- One PR, about 1,500 changed lines or fewer (PR-SIZE-1).

## Comments
