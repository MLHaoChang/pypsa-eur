<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 16 — Report and export dead ends after a fresh run

Status: ready-for-agent
Impact: medium
Templates: DC, H2, microgrid (export); every template (report)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Download JSON disabled: "Not exportable: the stored report was cleared by a later solve — re-run the study to export it." (tooltip shows raw "GET /results/eh_reference_design"); staged and site templates export fine. Reports > Generate report fails: "No usable LLM profile: configure an LLM profile in Settings." Without a key the only deliverable is JSON/CSV (KEY-LATER-1).

## Why it matters

The last step of the journey, the report, fails or is disabled for most users.

## Suggested fix

Keep the study report after the run (or re-run on export); offer a non-LLM report.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Still true: `eh_review.py` still returns 'cleared by a later solve' and `api/reports.ts` still returns 'No usable LLM profile'. Scope: keep the study report after a later solve or re-run it on export, and replace the raw 'GET /results/eh_reference_design' tooltip with plain words. The non-LLM report is not in scope; it is a product call sent to the coordinator. Gated: GK B and UX. Blocked by: none.

Decision (owner, 2026-11-20, TE2E-16-PLAIN-1): yes, a plain template report built from the stored results, with no key needed. This adds to the scope above: Reports > Generate report works with no LLM profile. Acceptance: with no key set, Generate report produces a report from the stored results; the LLM report stays available when a profile exists; a test. Gated: GK B and UX.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): DC, H2, microgrid: Expert Reports says 'No reports yet' after a certified Guided run, and Expert Results tabs read 'Not run yet' until Run LOPF is clicked; Create evidence report then builds in about 3 s and Export .docx/.pdf are offered.

- Seen again 2026-11-21 (template walk, master 6b421b06): IPP, Expert › Reports: "No reports yet" after a run; Generate report ends in "No usable LLM profile: configure an LLM profile in Settings. (The active LLM profile (anthropic-sonnet) has no API key configured ...)", so on a machine without a key the evidence report cannot be built and I could not check that it follows a field edit. TE2E-16-PLAIN-1 (plain report, no key) is not on this master. Ammonia Guided Results does offer "Download report (.docx)"; I did not open it.
