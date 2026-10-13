# U3 — the guided face in Guided mode (progress record)

Parent: `2026-10-05-one-investment-engine-two-faces.md`, §5 U3 row, §10 S2, §11 "Guided shell" (M7, M8,
the question-first rail). Session: GS (U3), started 2026-10-13 from master `8776f4a6`.

Owner decision on provenance (2026-10-13): S1a owns `n.meta["asset_params"]` and its single write helper.
S2 reads `services/asset_schema/provenance.get` and the descriptors in `services/asset_schema/fields.py`;
study code that writes pack defaults calls `provenance.record(..., status="default", source=<pack id/hash>)`.
No second store, no direct writes to `n.meta["asset_params"]`.

## Slices (one PR each)

| # | Slice | Needs | Status |
|---|---|---|---|
| A | Drop `PYPSAGUI_DECISION_STUDIES`; the auth-mode refusal stays, reworded (it no longer cites OPEN-ITEMS 1) | — | in PR |
| — | W4 refusal fix (mark post-U2 networks, refuse only unmarked ones, the user-typed price test) and the gate's four U3 one-line records | — | already on master (`96b7b6ef`); re-run green on `8776f4a6` |
| B | Decision-study entry in Guided's Start card: "Is my site reliable?" beside "Is this investment worth it?" (owner decision 5) | — | next |
| C | The six study chat tools with Guided confirmation tiers and `delegate()` steps | — | |
| D | The key-parameter list view | — | |
| E | M7 (Guided shell) and M8 (Guided without an API key) | — | |
| F | "Open in Expert" lands on the study's project (today's cards; switch to the S1b cards when they merge) | S1b for the final form | |
| G | S2: ledger rows as a view of the provenance store; pack defaults written through `provenance.record`; Expert edits mark ledger rows | S1a | waits for S1a |

## A — the flag

The flag ENABLED the study routes in local mode only; auth mode refused regardless "until OPEN-ITEMS 1 is
closed" (review v2 BC-6). #71 closed OPEN-ITEMS 1, so the flag goes and the desktop app always serves the
routes. Auth mode still refuses: the study runner, its study-owned base projects and forks were never
reviewed for several tenants on one server, and that review is not OPEN-ITEMS 1's. The message says so
instead of citing the closed item. (Asked of the owner through the coordinator; this PR takes the
recommended answer.) `decision_studies_disabled` is gone from the backend, the client and the copy.
