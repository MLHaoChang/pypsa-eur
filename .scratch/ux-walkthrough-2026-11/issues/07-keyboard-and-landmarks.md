<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Keyboard and landmarks: no main, no h1, focus order, chat-card focus

Status: ready-for-agent
Model tier: sonnet
Size: M
Gated: UX
Blocked by: #1556 merged for the chat-card part (it edits `ChatPanel.tsx`); the landmark and first-Tab part is free now
Likely files: `pypsa-gui/frontend/src/` app shell (main landmark, h1, first Tab stop), `ChatPanel.tsx` (choice-card focus, live region)
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: all steps
Severity: medium (the chat-card focus items are high once confirmed live; chat-dock rows are to confirm)
Send to: ui-cohesion

## What happens (Island Microgrid, Guided)

- No `<main>` landmark and no `<h1>`: the section title is an h2.
- On a fresh page the first Tab lands on the middle of the stepper ("Loads & demand"), not on the header or the first control; some later stops are unlabelled spans.
- Ctrl+K opens the palette and Esc closes it (good).
- Static read of the chat cards: a new choice card gets no focus and no live region (`ChatPanel.tsx:537-547, 2495-2506`); the scrollable detail box has no `tabIndex` (:589-594); "Check the 3D site" disables itself on click and drops focus (`AssistantResultCard.tsx:125`); the greeting's API-key form gets no focus (`ChatLaunchGreeting.tsx:243-246`).

## Done when

One main landmark and an h1 per screen, a predictable first Tab stop, and chat cards that take or announce focus.

## Journey fit

Applies to every step; no hand-off change.

## Comments

**2026-11-17 triage (SCALE-1).** Unowned; ui-cohesion 01 does not touch these files (it avoids `ChatPanel.tsx`). Two slices: (a) landmarks, h1 and first Tab stop; (b) chat-card focus and live region after #1556.
