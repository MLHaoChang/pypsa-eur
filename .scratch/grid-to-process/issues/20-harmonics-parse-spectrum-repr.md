<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `harmonics.parse_spectrum` puts Python repr quotes in user-facing text

Status: needs-triage
Type: bug (wording)
Found by: Logged during the harmonic/flicker work (G2P-17); owner list DOCS-1.

## Problem

The refusal reads `expected order:% pairs such as 5:20, got '5-20'`, with straight Python quotes. The UX rule is no `!r` quotes in user-visible text: plain words or curly quotes “…”. Fix the two messages and pin them with a test.

## Where to look

`gridspine/static/harmonics.py`, `parse_spectrum` (lines ~129 and ~137): `{part.strip()!r}` and `{k!r}` in the `ContractError` text.
