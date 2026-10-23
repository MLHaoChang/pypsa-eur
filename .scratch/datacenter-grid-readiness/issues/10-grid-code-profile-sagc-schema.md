<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Grid-code profile schema for SAGC-type codes, and page-range PDF upload

Status: done
Type: task
Blocked by: none (09 is the owner's priority, not a dependency)
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

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated (gridspine; UX for the profile UI), size M. Ship it as two PRs.**

- **10a (S–M): schema and checks.** `templates/grid_codes.py`, `templates/data/grid_codes.yaml`,
  `static/campus_compliance.py` and the profile editor (UX reviewer).
- **10b (S): page-range upload.** `services/campus_grid_code_service.py` and its upload UI (UX
  reviewer). It does not depend on 10a.

Nothing here touches frequency, so `Blocked by: 09` is dropped.

Rulings:

- **Keep `rvc_limit_pct` as-is, as the frequent tier.** It is in `_REQUIRED` and `_SINGLE_LIMITS`
  (`grid_codes.py` l.77–84), and `pypsa-gui/backend/services/adequacy/grid_readiness.py` reads it
  for the RVC row. Add an optional `rvc_limit_infrequent_pct` (with its clause and source) rather
  than turning the key into a pair. Old profiles and the readiness check then keep working
  unchanged. A profile without the infrequent tier reports that row as "no limit set".
- **Every new field is optional, with no default value.** D3-KEEP ships no TSO profile, so a blank
  profile is valid, and an unset limit gives verdict "unknown", the existing "no limit set" rule.
  `PROFILE_KEYS` gains each new key; unknown keys stay refused.
- **P-dependent Q band:** a list of `(p_pu, q_min_pu, q_max_pu)` points, interpolated linearly and
  checked at the PoC operating points the existing Q row already uses. Asymmetric PF limits are
  `pf_min_lagging` and `pf_min_leading`; the existing symmetric `q_range_demand` stays.
- **`dynamic_requirements`:** a list of `{kind, value, unit, clause, source}`. The report shows
  each as "needs RMS study"; there is no code path that can produce pass/fail for one, and a test
  asserts that.
- **Page ranges (10b):** accept `"1-40,120-180"`-style ranges, validated against the real page
  count. The 100-page cap applies to the selected pages. The size and type checks stay where they
  are (l.99 and l.341). With no range on a PDF over the cap, the error message names the range
  syntax.
- **G9 goes to a follow-up ticket now.** That is the per-unit reactive capability curve and the
  ±0.5 % deadband. "If small" is not something the implementer should judge, and 10a is already
  the larger half.
- **Not in scope:** teaching the grid-code PDF extraction the new fields. They are entered by hand,
  and that is a follow-up if wanted.
