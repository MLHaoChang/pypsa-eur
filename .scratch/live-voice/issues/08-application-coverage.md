<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application capability, parameter and navigation coverage

Status: ready-for-agent
Type: task

Read [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
and [coverage contract](../application-coverage.md). Generate a matrix from tools,
routes, service schemas and GUI bindings. The [inventory](../capability-inventory.json)
records 220 tools, 14 result tabs and 17 panels; behavioral/control mapping is pending.
Map every eligible tool, panel/subview/filter/editor, parameter type, job adapter,
evidence section, import/export/report operation and fixture. Resolve AR against
actual UI/module vocabulary. Fix adequacy/fmea navigation parity and audit other
aliases. Add missing shared typed tools over services. Respect ACL/mode/project
eligibility; no browser-click or provider-only workaround.

Test discovery and subsequent schema offering/execution for every eligible tool
under OpenAI's current 128-tool offer bound and verify Claude parity. Test all
navigation acknowledgments and parameter types/units/previews/freshness.
Keep matrix regeneration/check in CI. Done when every required interaction has a
mapping and passing contract, every family has a real journey assigned to issue
05, and remaining limitations block the full-coverage claim explicitly.

## Comments
