# `PUT /{name}/asset_health` ignores a foreign edit lock

**Date:** 2026-09-30
**Severity:** HIGH — cross-user destructive write to a bundled project sidecar.
**Status: FIXED** in `4eca8d2`, 2026-09-30. Found while verifying OPEN-ITEMS
items 2-4 against the tree that same day. Recorded first and fixed second, on a
separate instruction: it is a defect outside those three items, and the brief
for that work was explicit that anything found along the way is recorded, not
folded into someone else's commit. The reproduction below is the pre-fix
behaviour and is kept verbatim — it is the evidence, and
`tests/test_asset_health_foreign_lock.py` is the tripwire that now holds it.
**Scope:** the multi-tenant server only. `_check_project_lock` early-returns in
local mode, so the desktop build is unaffected.

## The mechanism

`routers/adequacy_worksheet.py:192`:

```python
@router.put("/{name}/asset_health")
def put_asset_health(body: AssetHealthPut,
                     project: AuthorizedProject = ProjectAccessDep) -> dict:
    try:
        return save_asset_health(project.directory, body.entries)
    except AssetHealthValidationError as exc:
        raise HTTPException(422, str(exc))
```

`ProjectAccessDep` answers "may this caller **see** this project". It does not
answer "may this caller **write** it while somebody else holds the edit lock" —
which is the distinction OPEN-ITEMS item 2 was opened about, for the two sibling
routes in this same file.

The handler takes no `db` and no `user`, so it has nothing to check a lock
*with*. It is mounted under `/api/projects`, deliberately absent from
`main._FOREIGN_LOCK_GATE_PREFIXES` because that family enforces the lock in its
handlers instead — so there is no middleware behind it either. Nothing anywhere
on this path consults the lock.

`save_asset_health` (`services/adequacy/asset_health.py:227`) is the same shape
as the two that were fixed: its own docstring says **"Validate, then replace the
ledger whole"**, and it bumps `version` (`current["version"] + 1`). So a
non-holder does not merge into the holder's ledger, it *replaces* it, and hands
the holder's client a higher version number, which that client reads as the newer
authoritative copy.

## Why it is the same defect as item 2, not a new one

`asset_health.json` is in `routers/projects._BUNDLE_FILES:99` — added there
deliberately, with a comment explaining that dropping it is not cosmetic because
`study_report._evidence_gaps` reads it (`services/adequacy/study_report.py:296,363`)
and a project without it reports every asset-level outage rate as `unsourced`.
That is precisely the propagation item 2 called out: the foreign write travels
into every later bundle export and snapshot, and it changes what the holder's
study report says about its own evidence quality.

So all four of item 2's stated aggravators apply unchanged: `ProjectAccessDep`
only, whole-document replace, a `version` bump the holder's client trusts, and a
`_BUNDLE_FILES` member.

## How it was missed

Ordering, and it is worth recording because it is the shape of the omission
rather than the omission itself:

| commit | date | what |
| --- | --- | --- |
| `a130636` | 2026-09-10 19:37 | adds the `asset_health` sidecar **and this PUT** |
| `30d6206` | — | the tree the per-route authorization audit was run against |
| `68e5f62` | 2026-09-12 19:01 | fixes the lock check on `worksheet` + `stress_scenarios` |

The audit named two routes; the fix closed exactly those two. The third route in
the same file, added two days before the fix, was never in the audit's route list
and so was never in the fix's scope. Nothing failed — which is the whole
complaint in OPEN-ITEMS item 6.

`tests/test_worksheet_foreign_lock.py`'s own module docstring says the fix it
guards is "the third instance of one miss" (after `routers/uploads.py`'s two).
This is the fourth. A per-route mechanism whose adoption is tracked by hand has
now been missed four times in the same router family, which is evidence for item
6 rather than a coincidence.

Note also that `_lock_target()` — the adapter the two fixed handlers use — already
exists in this file, a few lines above this handler. The fix is not blocked on
anything.

## Reproduced

Against `a9a1f5b`, two users in the SAME organization (the realistic intruder for
a lock test — this is not a cross-tenant ACL failure, the ACL is working).

A takes the lock and records one provenance entry:

```
A POST /api/projects/<p>/lock          -> 200
A PUT  /api/projects/<p>/asset_health  -> 200
   {"__schema__":1,"version":1,"entries":[{"component":"lines","name":"L1",
    "outage_rate_value":0.01,"method":"vendor_datasheet",
    "source_ref":"ACME datasheet rev C","measured_at":"2026-09-01",
    "confidence":"high", ...}]}
```

B, who does **not** hold the lock, replaces the ledger with an empty one:

```
B PUT /api/projects/<p>/asset_health {"entries": []}
   -> 200 {"__schema__":1,"version":2,"entries":[]}
```

A re-reads its own ledger:

```
A GET /api/projects/<p>/asset_health
   -> {"__schema__":1,"version":2,"entries":[]}
```

A's `ACME datasheet rev C` provenance is gone, and `version` went 1 -> 2, so A's
client treats the empty ledger as the newer authoritative copy rather than
noticing a conflict.

**The control, asserted in the same run rather than assumed** — B, still holding
no lock, tries the sibling PUT in the same router:

```
B PUT /api/projects/<p>/worksheet {"manual_rows":[],"overlays":{}}
   -> 409 project_locked
```

So the lock is live, the mechanism works, and `worksheet` honours it. Only
`asset_health` does not. That control is the difference between this finding and
a mis-set-up test: if the 409 had not come back, the 200 above would have proved
nothing about `asset_health`.

## The fix — applied in `4eca8d2`

Three lines, character-identical to what `put_worksheet` and
`put_stress_scenarios` already do immediately above it in the same file:

```python
@router.put("/{name}/asset_health")
def put_asset_health(body: AssetHealthPut,
                     project: AuthorizedProject = ProjectAccessDep,
                     db: DBSession = Depends(get_db),
                     user: User | None = Depends(optional_user)) -> dict:
    from routers.projects import _check_project_lock

    _lock = _lock_target(project)
    if _lock is not None:
        _check_project_lock(db, _lock, user)
    ...
```

Check-only, not acquire, for the reason `_check_project_lock`'s docstring gives:
writing a sidecar is not claiming the project, and an acquire here would leave a
120-second claim behind that outlives the request.

## Fix criteria — all met by `4eca8d2`

`tests/test_asset_health_foreign_lock.py`, written before the fix and proved red
on the property (`entries [...] -> []`, intruder got 200) while its three
sibling tests passed. Mutation-verified afterwards: removing just this guard,
leaving the two sibling guards intact so the control stays meaningful, turns the
subject red again on the same assertion.

* A non-holder's `PUT .../asset_health` is refused 409 `project_locked` while
  another user holds the lock.
* The holder's own PUT still succeeds (the check must not lock them out).
* A PUT on an **unlocked** project succeeds and leaves the project unlocked —
  check-only, not acquire.
* The test asserts the ledger's *entries* survive, not merely that a status code
  changed, and asserts a sibling route as a control so the test cannot pass while
  proving nothing. `tests/test_worksheet_foreign_lock.py` is the model and its
  `test_an_unlocked_project_is_writable_and_stays_unlocked` documents the
  `api_project`-holds-the-lock trap that made the first cut of that test fail on
  its own wrong premise.
* Mutation: removing the guard must turn the new test red.

## What would have caught it

Not a better audit — the audit was correct about the tree it read. The test item
6 proposes: one that enumerates write routes and fails when a write route is
reachable without any lock mechanism covering it, whether by middleware prefix or
in-handler check. `put_asset_health` would have been red the day `a130636`
landed, two days before the audit ran, and the fix commit would have had it in
scope. The enumeration is cheap — this finding was located by exactly that
script, run by hand over the five routers mounted under `/api/projects`.
