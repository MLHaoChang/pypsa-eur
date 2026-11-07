<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `harmonics.parse_spectrum` puts Python repr quotes in user-facing text

Status: ready-for-agent
Type: bug (wording)
Found by: Logged during the harmonic/flicker work (G2P-17); owner list DOCS-1.

## Problem

The refusal reads `expected order:% pairs such as 5:20, got '5-20'`, with straight Python quotes. The UX rule is no `!r` quotes in user-visible text: plain words or curly quotes “…”. Fix the two messages and pin them with a test.

## Where to look

`gridspine/static/harmonics.py`, `parse_spectrum` (lines ~129 and ~137): `{part.strip()!r}` and `{k!r}` in the `ContractError` text.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `gridspine/static/harmonics.py` `parse_spectrum` (~129, ~137) and its test.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK A.
- Size: S.
- Two `ContractError` texts; `git grep` the old text first. Sonnet is enough for a two-message wording fix.
