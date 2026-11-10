# Money path: the graph core, the asset card, the Economics table and the Guided ledger

Status: done (#1311)
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

### 2026-11-10 (~00:38 CET): GK B plan-delta GO, conditions G1–G7

GK B (session_01G7qCuiZkP4zBw3TJniXR4v) gave the slice's plan delta GO, on top of P1–P8. Caching the ledger verdict on
`_fork_snapshot`'s path+mtime+size key is acceptable under P5: the verdict's only non-network inputs are the packaged
Europe reference and pricing's constants (`SCALED_RULES`, `_CLASS`).

- **G1 (P5)** The PR body names those deploy-fixed inputs. A test: an Expert edit of `dc_ac_ratio` that rewrites the
  fork flips the ledger's `stale` on the next GET.
- **G2 (P1 vs P3)** On the wire an uncompared field keeps master's shape (no `stale` key on `/provenance`,
  `/asset_costs`); the ledger `stale` map lists only fields with a verdict. A queries-level test: a field with no
  recorded input and no scaling rule is `uncompared` and named in `Verdict.uncompared`.
- **G3 (P3)** The test-only oracle freezes master's `staleness` and the helpers it calls, verbatim, importing nothing
  from `provenance_graph` or the trimmed `provenance.py`; it covers a `fom_cost` inheriting the overnight annotation
  and a Link output-efficiency row.
- **G4** The ledger's stored bytes and hash are unchanged by the sibling `stale` (GET and PUT). The PR body lists
  every frontend consumer of the `/ledger` response types; vitest and tsc run on them.
- **G5 (P8)** Hand mutants besides `mutants.py`: each comparator branch, `derived_from` propagation, the unknown-kind
  `ValueError`, reserved kinds build nothing, uncompared-never-fresh, and each guard test once. Kills listed in the PR.
- **G6** At the trial merge, `smoke/assistant_inventory.py --check` and `assistant_route_parity.py --check`; the PR
  body lists the checks it migrates and the ones it leaves alone.
- **G7** The LedgerReview part goes to the UX reviewer; GK B reviews the wire.

### 2026-11-10 (~00:37 CET): UX plan GO for the ledger line, conditions

UX (session_01G7qCujt312zP7jy3ZddHnV): the ledger line uses the table's form, naming the asset ("<Component> · <name>
is out of date: …"); the hint names the asset, not "the option"; the line clears when its condition ends; shown once;
Expert shows no less (shots in both modes, or the PR says why the view is Guided-only).
