"""
Study reports — the client-facing document layer over the adequacy and
Energy Hub evidence.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
Assessment: docs/superpowers/assessments/2026-09-28-llm-report-generation-feasibility.md

Rules this package holds to (assessment §0):
* every table and figure is rendered from the evidence payload by code;
  a language model, when one is involved (WP3), writes prose only;
* a number the engines could not establish renders as "not established",
  never as 0 (ADR-0001);
* a section that was not established is stated, never omitted.
"""
