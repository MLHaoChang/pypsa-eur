# Item 6: make an ungated write path fail loudly

**Date:** 2026-10-05 · **Status:** steps 1 and 3 done (`a266dd5`, `828094e`); step 2 fixed independently by #76 (`e36c041`); step 4 is the owner's.
**Source:** OPEN-ITEMS item 6, from finding 3 of
`assessments/2026-09-12-per-route-authorization-audit.md`.

## What item 6 claimed, re-checked against the tree

| Claim (2026-09-12) | Now |
| --- | --- |
| `/api/results/` is outside the lock middleware | **Fixed**: it is in `_FOREIGN_LOCK_GATE_PREFIXES`, with the five study aborts exempted by name. |
| `ProjectAccessDep` is adopted by 6 of 23 routers | **Undercounted**: `ProjectDep` (`resolve_project_context`) is a second ACL dependency on the same `project_registry.resolve_project`. |
| `network`/`results`/`chat`/`simulation`/`io` should adopt it | **Mis-framed**: those routes act on the session's ACTIVE project and name no project in the path, so a path-param dependency cannot resolve one. Their ACL is established at `activate`; their lock is the middleware. |
| "a prefix list denies by omission" | **True, and found in three more places**: in-handler checks, and two hand lists on the chat seam. |

The item's own remedy was the right one: *"a test that fails when a route is
mounted under a prefix no mechanism covers. The omission is what needs to
become loud."* Migrating 171 routes to a dependency would not have caught
item 12, which was in a router that already used `ProjectAccessDep`.

## Ground truth on `24ed0b8`

162 write operations (321 total, held to OpenAPI):

| | routes |
| --- | --- |
| middleware-gated (75 refused for a non-holder, 14 explicitly exempt in `main.py`) | 89 |
| handler reaches a lock check (verified by following calls) | 23 |
| written decision in `ROUTE_POLICY` | 50 |
| **real gaps** | **0** |

Chat seam: 97 non-read tools. 47 are seam-gated, 30 inherit the decision of
the HTTP write route they map to, 13 have their own written decision, and
**7 are real gaps** (item 13). Both tables sum exactly; computed from the test
module, not counted by hand.

## Step 1: the policy test (done)

`tests/test_write_surface_lock_policy.py`. Every write route and non-read tool
must be gated, verifiably holder-checked, carry a written policy entry, or be a
`KNOWN_GAPS` entry pointing at an open item (a ratchet). Mutation-tested against
seven ways the gap comes back; see the `a266dd5` commit message.

## Step 2: fix item 13 (fixed independently by #76, `e36c041`)

#76 gates `clear_uploads`, `delete_upload`, `clear_chat_history`,
`start_campaign` and `end_campaign` at the seam, and all six exports through one
`_check_foreign_lock("export")` in `_save_agent_export`. It also splits the
list into `_NETWORK_SERVICE_CALL_MUTATORS` and the gated set, as proposed here.
Verified by trial-merging #76 into #80: the policy test's ratchet fires on
exactly the 7 `KNOWN_GAPS` entries and the 3 `TOOL_POLICY` entries it made
redundant. Whichever of #76 and #80 lands second deletes those 10 entries.
The original sketch follows.


Gate the seven upload-store tools at the chat seam. Delete their `KNOWN_GAPS`
lines; the ratchet forces this. Rename or split `_LOCK_GATE_SERVICE_CALL_MUTATORS`,
whose name says "network", and correct the stale docstring line that let it
through. Fix criteria are in the item 13 finding.

## Step 3: the same ratchet for the ACL dimension (done, `828094e`)

**Result: no gap.** All 75 project-naming routes reach
`project_acl.ensure_project_access`; so does every project-taking tool whose
argument names a project. `tests/test_project_route_acl_policy.py` now fails on
the next one that does not, and on any API family that uses `{name}` without
declaring what it names. The original sketch of this step follows.


"May this caller SEE this project" has no omission test. None has been found,
but none has been looked for systematically either. Shape: every route whose
path names a project must resolve it through `project_registry.resolve_project`
(via `ProjectAccessDep`, `ProjectDep`, or in-handler, verified the same way).
The hard part is that `{name}` is overloaded (component, series, project), so
the route set must be keyed on router/prefix, not on the parameter name.

## Step 4: decisions for the owner, not defects

Recorded in `ROUTE_POLICY`/`TOOL_POLICY` with the facts, so they are visible
rather than settled by silence:

1. **Gridspine studies** never take the edit lock; the UI opens one without
   `activate`. Two co-members can edit one study's config, last write wins,
   except while a job runs (`_refuse_while_active`). Should studies join the
   lock?
2. **Chat history** travels into snapshots and moves on Save-As, yet any
   co-member viewing a project can `import` into it or `clear_chat_history` on
   it. Appending your own turns is clearly fine. Should import and clear be
   refused for a non-holder?
3. **Adequacy campaigns**: a co-member can open or close the holder's
   in-memory study budget. It is not persisted, so this is low stakes, and it
   is recorded for completeness.

## Not doing

A wholesale migration to a per-route dependency. It is the right primitive
for routes that name a project, and most of those already use one. It does not
fit the active-project routers, and it would not by itself have made any of
the five misses fail.
