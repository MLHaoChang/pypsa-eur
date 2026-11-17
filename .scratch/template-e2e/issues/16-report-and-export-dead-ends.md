<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 16 — Report and export dead ends after a fresh run

Status: needs-triage
Impact: medium
Templates: DC, H2, microgrid (export); every template (report)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Download JSON disabled: "Not exportable: the stored report was cleared by a later solve — re-run the study to export it." (tooltip shows raw "GET /results/eh_reference_design"); staged and site templates export fine. Reports > Generate report fails: "No usable LLM profile: configure an LLM profile in Settings." Without a key the only deliverable is JSON/CSV (KEY-LATER-1).

## Why it matters

The last step of the journey, the report, fails or is disabled for most users.

## Suggested fix

Keep the study report after the run (or re-run on export); offer a non-LLM report.
