<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 — Opening a saved project and project housekeeping are rough

Status: needs-triage
Impact: low
Templates: concentrator (60+ s), others
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Projects list link or "Open existing" left "Unnamed Network / No project open" for 60+ s with a solve running. /app?project=<name> after clearing storage opens an empty "Unnamed Network" (the list uses a UUID). Solve Queue labels own deleted projects "Hidden — another organisation's project". Toasts: "At least one project tab must remain open" x3, "'X' no longer exists | Project not found". Autosave "off" for most templates, "on" for DC.

## Why it matters

Confusing but recoverable.

## Suggested fix

Loading state with progress; resolve name and id deep links; hide deleted projects from the queue.
