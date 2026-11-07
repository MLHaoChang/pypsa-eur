<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `attention.py` wording

Status: done
Type: wording
Found by: UX (9) review items; owner list DOCS-1.

## Problem

The attention-line wording needs a pass against the UX rules (colon not em dash, no repr quotes, "out of date" not "stale"). Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/harness/attention.py`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/harness/attention.py` and its tests.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B (harness text), UX (12) (wording).
- Size: S.
- Colon not em dash, no repr quotes, "out of date" not "stale". `git grep` the old strings first (a message rename).

### 2026-11-07: closed as already done

`7dd6574` (SH-07 PR B1: UX (9) wording fixes) already changed the one em-dash line in `attention.py` to a colon ("The study run finished: …"). Checked on master: no em dash, repr quote or "stale" is left in `harness/attention.py`, so there is no harness text for GK B (33) to review.
