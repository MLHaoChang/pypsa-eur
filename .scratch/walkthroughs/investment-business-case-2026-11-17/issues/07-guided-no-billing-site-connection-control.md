<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Guided has no control of its own for the billing site connection

Status: needs-triage
Severity: low (the data-centre template ships the site connection, so the walk passes)
Group: Investment and business case. Found in the issue 02 re-walk, 2026-11-23, master 443ec313.
Breaks: owner decision HOLISTIC-1 (no jump away and back). The old PR B plan said the billing control moves to section 3 (UX 2's call); GK B2 condition B3 applies (reuse SiteConnectionForm, refetch, no optimistic update).

## What

Guided section 3 (Connection & grid) shows "Connection point: Grid import (40 MW)", the connection for the reliability study. The commercial point of connection (Expert › Connection & grid › Site connection, "Save site connection") has no control in Guided. When it is missing, the investment-case needs checklist's "connection" button (InvestmentCaseRunPanel, `openSiteConnection`) jumps to the Expert editor.

## Repro

1. Create a project whose template ships no site connection (or remove it in Expert › Connection & grid › Site connection).
2. In Guided, open section 10 › Investment tile › Run the investment case. The needs list names the site connection, and its button leaves Guided.

## Expected

Section 3 carries the billing site connection (reusing SiteConnectionForm), named apart from the reliability connection point.
