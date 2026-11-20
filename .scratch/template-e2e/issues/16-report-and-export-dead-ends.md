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
