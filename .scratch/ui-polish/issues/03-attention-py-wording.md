<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `attention.py` wording

Status: ready-for-agent
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
