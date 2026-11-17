<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 50 — run_ic: wall-time recovery without a context, and bad deadlines

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: opus

Parent: [36-eh-site-run-investment-case.md](36-eh-site-run-investment-case.md) (GK B (64) OK on #1624, two non-blocking pre-existing items)

Two gaps in `harness/run_ic.py` that #1624 (eh_site@4) leaves as they were:

1. **The boot sweep without a context.** `recover` past a wall-time deadline with `ctx=None` (no bound project, so the record cannot be read) settles the step `ic_wall_time`. With a context whose record is lost or replaced, it settles `uncertain`, which stops the run (O5). The same unreadable record ends two ways depending on whether a context is bound. GK B needs to rule which one O5 requires, and a test must pin the ruling.
2. **Bad deadlines.** `_wall_of` and the recovery path accept `deadline_at` as any `int`/`float`, so a bool (`True` == 1.0) or NaN passes. Refuse a bool or non-finite `deadline_at` in both, and pin it with a test.

## Acceptance
- [ ] GK B rules on item 1, and a test pins it for both `ctx=None` and a lost record.
- [ ] A bool or NaN `deadline_at` is never read as a wall time in `_wall_of` or in recovery.
