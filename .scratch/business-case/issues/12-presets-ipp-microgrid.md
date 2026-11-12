<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — IPP, IPP hybrid and microgrid presets

Status: done (PR pending; GK B, GK A for linked outputs)
Type: task
Blocked by: 06, 07
Gated: yes (GK B; GK A for the linked outputs)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §6.6–§6.7 and [research.md](../research.md) §4–§5 first.

## What to build

IPP and IPP hybrid: the PPAs, merchant export and CfD as locked ledger rows
(never lines); capacity payment on `capacity:bess` with a typed de-rating; GOs;
land lease; insurance. Microgrid (both): tariff rows on `served_load:residential`
and `served_load:hospital_load`, EaaS fee off by default, opex rows.

## Tests

A linked export line on the IPP is refused `double_count` (G2); IPP business-case
revenue includes the PPA exactly once; unserved microgrid load earns no tariff and
no VoLL appears (B4).

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-12: carried from BC 06, GK B ruling C3 (served_load against the bill streams)

GK B (49) placed C3 with the presets (06 plan answer Q1 (a); its message named 07, the
preset slice, and this is the slice that owns the microgrid preset). Condition: the
microgrid preset's typed "Availability / EaaS fee" row's help says the locked rows
"from the energy model" and the help are what stop duplication. A linked
`served_load.<load>` against a valued `eaas_fee` is refused `double_count:eaas_fee`
since 06 (`linked_outputs.ledger_clash`).

### 2026-11-12: hand-off (builder)

Built: `project_templates/business_case_presets/eh_ipp_hybrid.json` and `eh_microgrid.json` (covers `eh_microgrid_staged`), both in `presets.py` VERSIONS; tests in `tests/test_business_case_presets_ipp_microgrid.py` (G2 export refusal, PPA once, B4 unserved, C3 refusal and help text).

Choices to review:
- `eh_ipp` is not a registered template (only `eh_ipp_hybrid`), so there is no `eh_ipp` preset.
- The IPP de-rating is the capacity row's `linked_share`; left empty (means 1) with a note, never a default.
- Capacity hint is the GB [S] range (20-60 GBP/kW-yr); the Italian [U] figure is left out.
- No pack default for insurance, maintenance or land: the pack's only insurance share is data-centre specific (BC 11 precedent). Each preset adds a typed "Plant capex" lump as the base of the insurance (and microgrid maintenance) share, since `share_of:capex` reads only the case's own capex lines.
- The EaaS fee is an offered row (off by default); its help carries the C3 wording.
- BC 13 (concentrator) is untouched.
