# 01d — Name the units holding the operating reserve in each binding hour

Status: done

Follow-up from homer-parity 01c (UX 21, condition 3). The 01c view shows the aggregate reserve held per binding hour, plus the storage that is not counted.

## Wanted

`GET /results/operating_reserve` returns, per binding snapshot, the generators and storage units holding the reserve and the MW each holds; the Results › Capacity expansion "Operating reserve" section shows them per hour.

## Acceptance

- The route payload carries the per-unit headroom for the binding snapshots only, from the solved values.
- The section lists them in plain words; UX OK on the head.

## Chat tool

`GET /api/results/operating_reserve` has no typed read-only chat tool yet: the assistant cannot read the reserve's binding hours. Add one (harness catalogue, route parity) in this issue; until then the route is declared held-by-spec against this file.

## Comments

GK B (H1–H9) on the plan, and how each is met:
- H1: units are the terms `held` sums, in the same loop (`parts`); tests pin the sum with `pytest.approx(rel=1e-12)` (summation order differs from per-unit order) including a 1e6 MW unit beside a 1e-6 MW one.
- H2: a unit missing from the dispatch gives the existing named reason and no rows (test per unit: a battery without `state_of_charge`; a generator missing from `p`).
- H3: committable unit off that hour: binds with `holders == []`; storage headroom is the held formula's own bound (test with state of charge); holders never list what `held` did not count.
- H4: sort by `held_mw` desc then `name`; tie tested; `kind` is set by the loop's component class.
- H5: non-binding rows have no `holders` key (exact `"holders" not in row`); binding uses 01c's own `binds`.
- H6: `get_results(result_kind="operating_reserve")` (project-scoped like the other results kinds; 204 reads `no_data`); binding hours only, 24 per page via `offset`/`limit`, 5 holders each, with `hours_total`, `binding_hours_total`, `holders_total` as true totals. No new error kind.
- H7: the `operating_reserve_result` not-via-chat row and 01c's held-by-spec declaration are removed; positive test `test_the_chat_tool_answers_the_reserve_goal`. Rebase on #1566 when it merges.
- H8: same solve record and `_with_freshness` as every results read; no second path.
- H9: the TS type follows the route's shape (`holders?: ReserveHolder[]`).
UX: Expert-only (the `capex` tab's `expertOnly` flag); units joined with " · "; fold past five with "(N more) Show all"; a binding hour with no holders reads "No unit had spare capacity to hold reserve".
