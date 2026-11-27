<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked plausibility findings: a bounded plausibility evidence/profile adapter

Status: needs-triage
Type: task
Blocked by: #851 (Codex K's native plausibility rules)

Moved out of [issue 10](10-metric-calculation-and-validity.md) on 2026-11-26 (owner Q3: "New issue blocked by #851"), so issue 10 can close.

Named by GK B's amendment on #851 (2026-10-28): Codex K ships the plausibility rules native and read-only, and checked publication of its findings is deferred here. Owned by the Assurance track, gated by GK B.

**Checked plausibility findings: a bounded plausibility evidence/profile adapter.** Acceptance:
- only `get_results(result_kind="plausibility")` is admitted; no blanket `get_results` whitelist, and the general guard on ordinary results reads stays closed;
- a valid finding is published as a positive control;
- LC-T19 injected extra claims are withheld;
- the stored record is MAC'd like #850;
- lookup re-derives the findings from the recorded result.

Study-harness issue 05's LC-T19 line depends on this item.
