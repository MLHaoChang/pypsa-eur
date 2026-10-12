# Campus equipment in the investment case: the last mile

Status: ready-for-agent
Written: 2026-10-12, by the campus electrical session, as a handoff (owner's choice).

## In one paragraph

A hub (capacity-expansion) project can run a **campus electrical study** that buys transformers, cables,
compensation and switchgear from an asset library. That purchase must reach the project's **investment case
(IC)** as *extra owner assets*: capex, fixed O&M, replacements and terminal value next to the batteries and PV.
Both ends exist: the campus side produces the list (`extra_owner_assets`), and the IC engine accepts it
(`build_finance_case(..., extra_assets=...)`). **Nothing connects them yet**: no route passes `extra_assets`, so
no investment case ever contains campus equipment, and the Investment tab shows none. This spec is that
connection. It is written for whichever session picks it up; it assumes no knowledge of the conversations that
produced it.

## Open ownership question (state it, do not guess)

The IC session's findings say who builds the route "is the campus session's answer" (U1 landing plan §8; findings
`docs/superpowers/findings/2026-10-07-ic-g1-g2-campus-equipment.md`, Known limits 7). The owner chose to answer
with **this handoff issue** rather than have the campus session edit IC-owned files. So the route lives in an
IC-owned file (`pypsa-gui/backend/routers/results.py`, the IC P4 section) and is to be written by whichever session
the owner assigns: the IC session, or the campus session with explicit leave to touch that section. **Whoever picks
it up: confirm with the owner before editing IC-owned files if you are not the IC session.**

## What is already done (do not redo)

| Piece | Where | Owner |
|---|---|---|
| Producer: the list of extra owner assets, its hash, its staleness, its method | `pypsa-gui/backend/services/campus_electrical_service.py`: `extra_owner_assets(project)`, `campus_study_hash(project)`, `owner_assets_response(project)` | campus |
| Producer route | `GET /api/campus-electrical/{name}/owner-assets` in `pypsa-gui/backend/routers/campus_electrical.py` | campus |
| Which campus result feeds the case: "MILP when fresh, else least cost" (owner, 2026-10-12) | same service, `_owner_source`; `meta.study_method`, `study_method`, `get_state.owner_assets_method` | campus |
| The campus panel says which result feeds the case | `pypsa-gui/frontend/src/pages/CampusInvestmentSection.tsx` (`invest-ic-source` line) | campus |
| `ExtraOwnerAsset` type (frozen; validates; normalises dict parts) | `pypsa-gui/backend/services/finance/case.py` | IC |
| `build_finance_case(n, cfg, fin, *, result_df, lost_load=None, owner=None, extra_assets=())` | `pypsa-gui/backend/services/results/finance_case.py` | IC |
| Extra assets as owner capex, FOM line, COD by build year, tax class, incentive exclusion, double-count flag, report `extra_assets` block, xlsx rows, `FinanceCase.extra_assets` hashed only when non-empty | IC G2 (PR #101) | IC |
| Defaults-pack version `2026-10-07` with the 243 campus rows (D1b) | `pypsa-gui/backend/services/library/defaults_pack/versions/2026-10-07/` | IC |

Plan references: `docs/superpowers/plans/2026-10-05-gridspine-campus-electrical.md`, sections "Part three as built
(2026-10-07)" → "D2 producer: done", "D1b", and "The investment case's source (2026-10-12)".

## The producer contract (copy; authoritative text is the docstring of `extra_owner_assets`)

Call, with the project's DB row (`db.models.Project`), all in `services.campus_electrical_service`:

- `owner_assets_response(project) -> {"assets": [...], "source_hash": str | None, "stale": bool,
  "study_method": "milp" | "least_cost" | None}`. **Use this one** in the route: one call, one consistent snapshot.
- `extra_owner_assets(project) -> list[dict]` and `campus_study_hash(project) -> str | None` are its parts.
- Every function refuses a project that is not a capacity-expansion (hub) project with `HTTPException(409)`, and
  answers 503 when the build has no gridspine engine. **The route must not call them for other projects** (check
  `services.gridspine_service.kind_of(project) == CAPACITY_EXPANSION` first, and treat 503 as "no campus assets").

Each entry; the eight top-level keys are exactly the fields of `ExtraOwnerAsset`, everything else is under `meta`:

```json
{
  "name": "campus:TR_132_33_63#1",
  "kind": "transformer",
  "basis": "lump",
  "quantity": 2.0,
  "parts": [{"name": "investment", "upfront_per_unit": 2500000.0, "lifetime": 40.0, "fom_share": 0.015}],
  "build_year": 2030,
  "source": "campus_study",
  "source_hash": "3f9a1c5e07b2d864",
  "meta": {"need": "transformer GRID_IMPORT", "library_id": "TR_132_33_63", "units": 2, "length_km": null,
           "currency": "EUR", "price_year": 2026, "provenance": "assumed", "illustrative": true,
           "study_method": "least_cost"}
}
```

- `name`: `campus:<library_id>#<k>`, k the 1-based occurrence of that id, unique in the list.
- `kind`: `transformer | cable | capacitor_bank | shunt_reactor | statcom | switchgear`.
- `basis` / `quantity`: `lump` (units), `per_km` (units × length_km, a cable), `per_bay` (bays, switchgear).
- `parts`: one part `investment`; `upfront_per_unit` is the library's overnight cost **per basis unit**;
  `quantity × upfront_per_unit` is the item's total. `lifetime`, `fom_share` from the library.
- **Money**: `meta.currency` and `meta.price_year` are the campus library's. **No escalation is applied** on the
  campus side. The shipped library is EUR, 2026 money, every cost `assumed` (so `illustrative: true`).
- `build_year`: the campus investment period. `source`: `"campus_study"`.
- `meta.study_method`: `"milp"` when the joint optimisation (MILP) feeds the case, `"least_cost"` otherwise.

Build the IC objects exactly as the IC tests do (`tests/test_finance_case_extra_assets.py::_from_dicts`):

```python
fields = [f.name for f in dataclasses.fields(ExtraOwnerAsset)]
extras = tuple(ExtraOwnerAsset(**{k: d[k] for k in fields}) for d in body["assets"])
```

### `source_hash`, `stale` and `study_method`

- `source_hash` = sha256[:16] over the files of the run the assets come from: `campus_investment.csv`,
  `campus_assets_used.yaml`, `campus.yaml`, `campus_cost_basis.json` (when present), each as name, NUL, bytes. For
  the MILP the digest is prefixed with `study_method`, NUL, `milp`; the least-cost hash has no prefix. It changes
  when the campus, the library, the discount rate, the run, **or the source** (MILP finishing, or going stale)
  changes. The same on every entry; `None` when there are no assets.
- `stale` = the campus results no longer match the campus file, the project's network, the library or the cost
  basis (the least-cost run's flag; a MILP is only ever used when fresh, so its `stale` is false). **A stale study
  is still listed**, flagged: refusing it is the IC route's job.
- Which run: the MILP's (`run_milp/`) when it finished with a point of its own (a fallback to the least-cost
  choice, cancelled or not, does not count) and is not stale; otherwise the least-cost `run/`.

## What is to be built

1. **The route** (issue 01): `POST /results/investment_case` (IC P4, `routers/results.py`) reads the loaded
   project's campus owner assets in the request, refuses a stale study, refuses (or converts) a money mismatch,
   passes `extra_assets` into `build_finance_case` through `_ic_build_case`, and puts `source_hash` and
   `study_method` into the assumptions digest so the report goes stale when the campus study changes or switches
   source.
2. **The Investment tab** (issue 02): show the report's `extra_assets` block, with the study method and the
   illustrative flag.

## Owner defaults in force (from the IC plan, U1 §8)

- **B**: the IC refuses a stale campus study.
- **C**: the IC refuses a `build_year` after the case COD (already in `build_finance_case`: `cod_mismatch`).
- Electrical cost is never fed back into the capacity expansion; the PyPSA project is untouched.

## Out of scope

- Any change to the campus producer or its contract (campus-owned; ask the campus session).
- Seeding the campus library from the defaults pack (D1, later).
- Escalating campus costs between money years (no index exists for them; refuse instead, see issue 01).
