<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Expert shows a tool's Progress block twice

Status: needs-triage
Type: task
Found by: live-voice 11c-2 (#1556) shots, UX (21) follow-up. It predates that PR.

## What is wrong

In Expert the chat shows two rows for one tool call: the request row (`→ run_simulation`)
and the result row. Both carry the same `tool_use_id`, and `ToolProgressDetails` renders
under each, so the Progress block appears twice (open on one, collapsed on the other).
Guided hides the request row, so it shows once there. 11c-2 already places its monitor
line only under the last row of its tool (`ChatPanel.tsx`, `monitorRowIndex`).

## What done looks like

- The Progress block renders once per tool call, under the last row of that tool, in
  both modes; a test with a request row and a result row pins it.
- Shots in Guided and Expert.

## Comments
