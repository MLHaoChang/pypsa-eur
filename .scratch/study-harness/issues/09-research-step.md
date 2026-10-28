<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Research step: provider web search, cost cap, source ledger

Status: ready-for-agent
Type: task
Blocked by: 04
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-RESEARCH-1, TOOLSEL-1, AI-567b (values shown beside their source and confirmed before they are written)
Design: [design.md](../design.md) §8

## What to build

1. A provider capability for web search/fetch in `harness/providers` (no vendor words outside it), offered only in research turns (stage 2 `researchable` gaps, stage 6 sourced ranges). Not a catalogue tool.
2. Cost cap from `envelope.research.cost_cap_usd`, counted from provider usage; at the cap research stops and the gaps are listed.
3. Source ledger rows (design §8); a value enters the model only through the edit card (or `ledger.apply_user_row` for a decision-study row) with its source beside it, always carded; run `needs_user` with `research_confirm` while values wait.
4. Unit check: refuse a value in another unit.
5. A provider without web search: `not_available`, defaults used, gap listed.

## Acceptance criteria

- [ ] A chat turn never offers web search; a research turn does.
- [ ] A researched tariff value is shown with url and quote and is written only after the card click; inside a granted envelope it is still carded.
- [ ] Cost over the cap stops research mid-stage; `run.budget.used.research_usd` is from provider usage.
- [ ] A rejected value stays `rejected` in the ledger and the report.
- [ ] Layout test: no vendor word outside `harness/providers`.

## Likely files

`pypsa-gui/backend/harness/providers/`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_envelope.py`, `pypsa-gui/backend/tests/test_harness_research.py` (new)

## Comments
