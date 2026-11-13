# Reserve billing: read the commitments, the weights and the frame in one bracket

Status: ready-for-agent
Model tier: sonnet.
Size: S.
Blocked by: 04b (#1478, `runs.locked_read` and the callable digest).
Gated: GK B.
Likely files:
- `pypsa-gui/backend/services/results/billing.py` (`reserve_lines`)
- `pypsa-gui/backend/services/results/reserves.py` (`reserve_view`)
- `pypsa-gui/backend/services/solver/reserve_products.py` (`solved`, `reserve_weights`)
Type: bug

## What

This predates 04b. GK B (54) found it while reviewing #1478.

- `billing.reserve_lines` (~:254-255) reads `reserve_weights(n)` after `RP.solved(...)` returns, outside the edit-counter and network bracket that `solved()` reads under. If a reweighting lands right after the bracket, the old commitments are priced at the new hours. GK B probed an amount of 400.0 against a base of 200.0, with flags `[]`. The next read is stale, so the wrong amount shows for one poll.
- `reserve_view` calls `solved()` itself (~:96), then calls `reserve_lines`, which calls `solved()` again (~:106). The view's frame and its lines can therefore come from different moments.

## Acceptance

- The frame, the flags and the weights a line is priced at come from one read under one bracket, and `reserve_view` reads once and hands the same read to its lines.
- A test lands a reweighting right after the bracket and shows that the amount is never priced at weights other than the ones the frame was read with. It is red today.
- The flags and the order of today's callers are unchanged (04b's W1 table).

## Comments
