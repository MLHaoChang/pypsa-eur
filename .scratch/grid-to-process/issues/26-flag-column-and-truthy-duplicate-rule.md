<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `process_uc.flag_column` and `process_chain._truthy` are two copies of the string-flag rule

Status: done
Type: task
Found by: G2P-19 (#1189, merged), relayed by the coordinator; owner list DOCS-1.

## Problem

`process_uc.flag_column` now parses string flags: "False", "0" and "" are false; "true", "1", "1.0" and "yes" are true. gridspine's `process_chain._truthy` is a second copy of that rule without "yes". Unify them into one function. `docs/agents/review-gk.md` (point 5, added in #1205) says `flag_column` agrees with `_truthy`, pinned per string; it should name "yes" once they are unified.

## Where to look

`process_uc.flag_column` and gridspine `process_chain._truthy`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `process_uc.flag_column`, `gridspine/drivers/process_chain.py:54` (`_truthy`), `docs/agents/review-gk.md` point 5.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK A.
- Size: S.
- One shared function; keep "yes" true; pin per string; update review-gk.md point 5 to name "yes".
