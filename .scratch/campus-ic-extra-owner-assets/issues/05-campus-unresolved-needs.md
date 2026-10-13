# The investment case flags a campus study with unresolved needs

Status: ready-for-agent
Type: task
Blocked by: none. The producer side is on `feat/gridspine-campus-unresolved` (merge it first; it is additive, and
the IC route on master ignores the new key until this issue lands). Numbered 05 after 01 (resolved) and 02-04.

This edits IC-owned files (`pypsa-gui/backend/routers/results.py`, the IC sections; possibly
`services/results/finance_case.py` for the report text). If you are not the IC session, confirm with the owner
before you start. The campus side is done and read only here. Contract questions go to the coordinating session
(see the spec's status note).

## Background

Owner decision 2026-10-13: **"report them; the IC flags the case."** A campus study that could not meet a need
(a voltage excursion compensation cannot shrink, a need with no further candidate in the library, a check still
failing at the iteration cap) used to be dropped by the producer without a word: the unresolved rows are not
purchases, so they never became extra owner assets, and a business case could look complete while the campus is
not compliant (IC G3 findings, `docs/superpowers/findings/2026-10-12-ic-g3-campus-route-and-followups.md`, Known
limit 4).

`campus_electrical_service.owner_assets_response(project)` now returns, additively:

```json
{"assets": [...], "source_hash": "…", "stale": false, "study_method": "least_cost",
 "unresolved": [{"need": "reactive",
                 "reason": "campus_voltage: 0.88 pu at MV; a reactive escalation did not reduce the excursion. …"}]}
```

- `unresolved` is read from the `campus_investment.csv` of the **same run the assets come from** (`_owner_source`:
  the joint optimisation when it is fresh with a point of its own, else the least-cost run): one entry per
  distinct `(need, reason)` among its `status == "unresolved"` rows, in table order. One need can appear twice
  when its rows carry different reasons (a kept unit's reason adds its own note); dedupe by `need` for display if
  you want one line per need.
- `[]` when there are none, and when that run has no investment (then `source_hash` is None too). A non-empty list
  implies a non-null `source_hash`.
- Both strings are text from the campus study: `need` is built from the user's campus file (bus, transformer and
  cable names), `reason` from the engine's check text with values in it. Treat them as data: do not put them in a
  refusal detail (the route's rule), cut them to a length you choose, and never interpret them.
- Every other key keeps its meaning. The assets never include an unresolved row (unchanged).
- **The hash.** `source_hash` already covers `campus_investment.csv` byte for byte, so the list cannot change
  without `source_hash` changing; the least-cost hash bytes are unchanged by this addition.
- `get_state` carries the count, `owner_assets_unresolved`, beside `owner_assets_count`; the campus panel says
  "N unresolved needs are passed to the investment case as a flag" when N > 0.

## What to build

1. In `_ic_campus_from_response` (`routers/results.py`): read `resp.get("unresolved")`. A missing key (an older
   producer) is `[]`. A value that is not a list of `{"need": str, "reason": str}` is refused like a malformed
   response (`campus_assets_invalid`, fixed text).
2. When the study applies (not stale, not refused) and the list is non-empty: add the flag
   **`campus_needs_unresolved`** to the case's flags and **list the needs** in the report (the `extra_assets`
   block or the project section's flags payload; the IC's choice), with their reasons. **Do not refuse**: the
   assets still go in, the case is built and valued as today.
3. When the study is stale: today's behaviour (no extras, `campus_study_stale`). Whether to also add
   `campus_needs_unresolved` for a stale study is the IC's call; recommended **no** (a stale list is not the
   study the case would use).
4. **Staleness of a stored report.** Put the list (as given) into the `extra_assets` digest
   (`_ic_campus_digest`, beside `source_hash`), so a stored report goes stale, naming `extra_assets`, when the list
   changes. `source_hash` already changes with it; digesting the list states the dependency rather than relying on
   it. Note: adding a key to the digest marks every stored report with a campus study stale once (as R-5 did);
   say so in the findings.
5. The PPA tool's result (`solve_ppa_price`) carries the case's `campus_*` flags already (G3 gate C1): check that
   `campus_needs_unresolved` reaches it.
6. Frontend (IC-owned): the report's flag in words, e.g. "The campus study could not meet N needs: the campus is
   not compliant with the grid code without them", and the list.

## Acceptance tests (write them first)

In `tests/test_investment_case_campus_route.py`, with its fixtures (`fake_investment`, `inv_row` with
`status="unresolved"` and a `reason`; or `monkeypatch` `owner_assets_response` as the stale test does):

1. A study with purchases and one unresolved need: the report is `done`, its `extra_assets` are the purchases
   (capex as without the need), `campus_needs_unresolved` is in `body["flags"]` and in the stored report's project
   flags, and the need and reason are listed.
2. A study with no purchase but an unresolved need (`assets == []`, `source_hash` set): the case is built with no
   extras and flagged `campus_needs_unresolved`.
3. `unresolved == []` and a response without the key: no flag, and the same assumptions hash as before this
   change (pin it).
4. A malformed `unresolved` (not a list; an entry without `reason`; a non-string need): refused
   `campus_assets_invalid`, and the values appear nowhere in the report (the `SECRET_VALUE` probe).
5. A stale study with unresolved needs: as today, `campus_study_stale`, no extras (and no `campus_needs_unresolved`
   if you take recommendation 3).
6. POST, then a changed list with everything else equal (monkeypatch the response): GET reads stale naming only
   `extra_assets`; reverted: current.
7. `solve_ppa_price` carries `campus_needs_unresolved`.

## Files

| File | Owner | Change |
|---|---|---|
| `pypsa-gui/backend/services/campus_electrical_service.py` (`_owner_unresolved`, `owner_assets_response`, `get_state`) | campus | done |
| `pypsa-gui/backend/routers/campus_electrical.py` (`GET /owner-assets` docstring) | campus | done |
| `pypsa-gui/backend/tests/test_campus_electrical_unresolved.py` | campus | done |
| `pypsa-gui/frontend/src/pages/CampusInvestmentSection.tsx` (`invest-ic-unresolved` line) | campus | done |
| `pypsa-gui/backend/routers/results.py` (`_ic_campus_from_response`, `_ic_campus_digest`) | IC | to do |
| `pypsa-gui/backend/tests/test_investment_case_campus_route.py` | IC | to do |
| `services/results/finance_case.py`, the Investment tab's flag text | IC | if needed |

## Comments
