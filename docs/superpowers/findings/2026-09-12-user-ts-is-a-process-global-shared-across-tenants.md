# The user-timeseries store is a process global shared across tenants

**Date:** 2026-09-12
**Severity:** CRITICAL — cross-tenant data read AND cross-tenant data write.
**Status: OPEN, NOT FIXED.** The real fix is architectural (see "Why this is not
patched here"). Found by an independent QA review; reproduced independently
before being written up.
**Scope:** the multi-tenant server for the cross-tenant leak. The desktop build
is **also** affected, as a multi-project data-integrity bug.

## The mechanism

`services/user_timeseries.py:39`:

```python
_user_ts: dict[tuple[str, str, str], pd.Series] = {}
```

A process-wide dict keyed by `(component, attribute, column_name)`. **No org, no
project, no session.** The key comment explains why the key is per-column (index
alignment) and is silent on tenancy.

It is not a cache. It is authoritative in three directions:

* `GET /api/network/timeseries/{component}/{attribute}` **prefers** it over the
  network's own data (`routers/network_time_axis.py:1051,1062`).
* Every foreground save serialises it into that project's `user_ts.json`
  (`routers/projects.py:_serialize_user_ts`) and reapplies it onto the network
  before the netCDF export (`_reapply_user_ts_to_network`), so it lands in
  `network.nc` and therefore in solve results.
* Loading a project **replaces it wholesale**.

## Reproduced

Org A uploads a load profile for component `L1`. Org B — a **different
organization** — activates **its own** project `PB` and reads:

```
B activate own project -> 200 {"activated":"PB", "lock":{"holder_email":"other@example.com","yours":true}}
B GET /api/network/timeseries/loads/p_set
   -> 200 {"columns":["L1"],"data":[[777.0],[777.0],[777.0],[777.0]]}
B POST /api/projects/PB   (save) -> 200
B's <orgB>/<PB-uuid>/user_ts.json -> {"loads":{"p_set":{"L1":{... 777.0 ...}}}}
```

So A's uploaded demand profile is **readable** by another tenant and is
**persisted into that tenant's project storage**, baked into their `network.nc`
at the next save, and fed into their solve results. Symmetrically, A loading a
project wipes B's in-flight uploads.

`GET /api/network/loads` still shows B's own static `p_set: 100.0` — so the two
surfaces disagree, and the one that wins at export time is the leaked one.

## Why the existing mitigation does not cover this

The code knows the shape and mitigated only the **background** case:
`routers/projects.py` gates `persist_user_ts` for background saves, and
`_hydrate_context_from_disk` deliberately does not touch the store, saying the
store "belongs to the FOREGROUND ctx" and that per-ctx `_user_ts` will land "in a
later phase".

That reasoning holds for one desktop user. It does not survive tenancy: with one
process serving many signed-in sessions, **every** session is a foreground
context, so the ungated path is the normal path rather than the exception.

## Why this is not patched here

`_user_ts` has ~230 references across 13 production modules. Making it
per-`ProjectContext` is the actual fix and is a scoped piece of work with its own
plan and its own verification — not a line to add to an unrelated PR.

A narrow containment exists and is worth considering, but it must not be mistaken
for the fix: `load_project` already clears the store when the target project has
no `user_ts.json` (`routers/projects.py:1101`), and `activate_project` does not.
Restoring-or-clearing on activation (guarded to skip a same-project
re-activation, so unsaved uploads are not discarded) would close the path this
reproduction uses.

It would **not** close the class. Two concurrent sessions on different projects
still share one dict, so whoever activated last wins, and a read by the other
still crosses the tenant boundary. A mitigation that closes the demonstrated path
while leaving the class open is worse than a clearly-open finding, because it
retires the alarm without retiring the risk. Hence: recorded, not half-fixed.

## What a fix has to establish

1. A series uploaded in one project is never readable from another project — same
   org or not.
2. A save writes only series belonging to the project being saved.
3. Loading or activating a project cannot discard another session's in-flight
   uploads.
4. A test that fails if a second tenant can read the first tenant's column — the
   property, asserted directly, rather than the absence of a known path.
