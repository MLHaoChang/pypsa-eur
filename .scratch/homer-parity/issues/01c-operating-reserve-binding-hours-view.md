# 01c — Show the hours where the operating reserve binds

Status: needs-triage

Follow-up from homer-parity 01b (#1521, engine and route; 01b-2, inputs). UX 21 asked for it as its own issue, reviewed when it exists.

## Problem

`GET /results/operating_reserve` (01b-1) returns, per snapshot, the reserve required and held and whether the reserve binds, but no Results card reads it. A user who sets a reserve sees its cost in the totals and never the hours where it decided the dispatch.

## Wanted

- A Results view listing the snapshots where the reserve binds (required, held, the units holding it), with the reserve shares it was solved with; hidden when the reserve is off.
- Placement agreed with the UX reviewer before building (the project workflow's Results section).

## Acceptance

- The view renders from the route for a solve with the reserve on and stays hidden when it is off; UX OK on the head.
