<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 27 — IPP: Guided has no run path and the results hand-off is a dead end

Status: needs-triage
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: run → results for a generation-only site.

## What

Guided sections 8 and 9 say 'Not offered for this site: Nothing to make adequate', Run study is disabled. After Expert Run LOPF (about 12 s solve) Guided Results still says 'Not run yet … Go to Goal and run the study'; Improve is disabled and Guided has no Download report. The Goal panel asks for an allowed shortfall and a price of undelivered energy for a site with no load. Total system cost shows -EUR 4.55 M (net revenue, not labelled). The Expert-only report says six times 'no ReferenceDesignReport … run the study first (Goal step)'. See 11 (no-verdict) and 14.

## Acceptance

- Guided offers an IPP run (or a clear Expert hand-off) and its results show the LOPF outcome, NPV and IRR.
- No load-only goal inputs on a generation-only site; net revenue is labelled; the report omits the reliability text.
