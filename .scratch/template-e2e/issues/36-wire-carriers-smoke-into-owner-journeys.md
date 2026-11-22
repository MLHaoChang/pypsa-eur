<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 36 — smoke-template-journey-carriers.mjs is not wired into CI

Status: needs-triage

Open item from the template-e2e 02 stack hand-off.

## Problem

`smoke-template-journey-carriers.mjs` exists but `.github/workflows/test.yaml` owner-journeys does not run it.

## Wanted

Run it in owner-journeys after the tour line, next to `smoke-template-journey-connection.mjs`.
