<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 — A tool the assistant just discovered is offered on its next request

Status: ready-for-agent
Outsourced: Codex (2026-10-22) — branch outsource/discovered-tools-offered
Type: task
Baseline: master `514c8feede1177b520a4dde74a4dffaf12cf696a`
Source: PR #567 assessment (CAP-04, HS-01); brief `.scratch/harness/outsource/A-discovered-tools-offered.md`

## Problem

On the official OpenAI profile only 128 of the 287 tools are offered per
request (`harness/providers/wiring.py::_bounded_tools`). `find_capabilities`
names the right tool, but neither the ranking nor the per-round refresh
(`harness/loop.py:2288`) uses that result. The model's next call can therefore
be refused `tool_not_offered` in the same turn.

## Scope

1. `ChatSession.discovered_tools`: the names from the last goal-mode
   `find_capabilities` call (replaced, never accumulated).
2. `_bounded_tools` ranks those names after controls and plan tools.
3. A `find_capabilities` batch refreshes the next request's offer.
4. Discovery grants nothing: toolset, eligibility, restrictions, cards and
   the dispatch allowlist are unchanged. Other providers are unchanged.

## Done when

A FakeProvider turn (round 1 `find_capabilities`, round 2 the discovered tool)
runs the tool with no `tool_not_offered`; the existing
`tests/test_openai_tool_selection.py` passes unchanged; the README paragraph
and the release-note fragment are in the PR.

## Comments
