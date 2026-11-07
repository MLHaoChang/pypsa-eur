<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Lock refusal names the tool in Python-repr quotes

Status: ready-for-agent
Type: bug (wording)
Found by: UX (12), S4b review (#1204), relayed by the coordinator.
Owner: AA-04 follow-up (job holds / foreign lock).

## Problem

`services/chat_tools._check_foreign_lock` builds both lock refusals (project_locked, about line 10088, and project_lock_unavailable, about line 10111) as "so {tool_name!r} was not run". That puts a Python-repr tool id in user-visible text, against review-ux.md (no Python-repr quotes; engine identifiers only in a Source column).

## Fix

Say "so it was not run" in both messages, and update the tests that pin the old text. Reviewers: GK B (harness) and UX.
