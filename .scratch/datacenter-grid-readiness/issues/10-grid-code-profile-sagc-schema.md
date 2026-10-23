<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Grid-code profile schema for SAGC-type codes, and page-range PDF upload

Status: needs-triage
Type: task
Blocked by: 09
Gated: **yes** (gridspine; UX reviewer for the profile UI)
Model tier: opus
Size: S–M
Owner decisions: SAGC-1 (G3, G9 if small), D3-KEEP, 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

The grid-code profile (`gridspine/templates/grid_codes.py`, `static/campus_compliance.py`) cannot
hold several requirement shapes SAGC uses. The grid-code PDF upload refuses long codes:
`MAX_PDF_PAGES = 100` at `pypsa-gui/backend/services/campus_grid_code_service.py:99`, and SAGC is
292 pages.

D3-KEEP: no TSO profile ships. SAGC limits are entered per project in a blank profile. This ticket
only adds the fields such a code needs.

## What to build

1. **Schema fields:**
   - two-tier rapid voltage change (RVC) limit (e.g. 1 % frequent / 3 % infrequent);
   - a P-dependent Q capability band (Q_min(P), Q_max(P) at the PoC);
   - asymmetric power-factor limits (leading ≠ lagging);
   - `dynamic_requirements` rows (VRT, post-fault recovery, dynamic voltage support, RoCoF
     withstand, droop, fast frequency response, POD). Each is recorded and shown as
     **"needs RMS study"**, never checked as passed (dynamics stay in PSS/E and PSCAD, SAGC-1).
2. **Checks** for the static fields, in `campus_compliance.py`, with limit, margin and band like
   the existing rows.
3. **PDF upload:** let the user pick a page range (or several) so a code over 100 pages can be
   read. The 100-page cap still applies to the chosen pages. Keep the size and type checks.
4. **G9, if small:** a reactive capability curve per unit and a ±0.5 % voltage-control deadband.
   Otherwise write it up as a follow-up ticket.

## Done when

- A blank profile can hold every field above, and the profile UI edits them (UX reviewer OK).
- Dynamic rows show "needs RMS study" in the report, never pass/fail.
- A 292-page PDF uploads with a page range; one with no range and more than 100 pages is still
  refused with a message that says how to pick a range.
