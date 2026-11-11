<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 37 — Plan card renders the intake and sends its digest

Status: done (#1379)
Type: task
Gated: yes (GK B plan, UX review)
Model tier: sonnet
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) S1, S1a)
Design: [21-chat-data-intake.md](21-chat-data-intake.md) "Confirmation, never silent trust"

The backend creates a project from an intake only on a verdict that carries the
intake digest the card showed, for both verdicts (issue 07 B3b, S1a); an absent
digest refuses `intake_stale`. The frontend plan-review card
(`EnvelopeSection`, `ChatPanel`, `chatStore`) renders no intake rows, no setup
line and sends no `intake_digest`, so the GUI cannot start an intake run yet.

Build: render `envelope.setup` as the card's first row ("Creates project
<name> from the <template> template", digest short form in Expert) and the
intake rows with their sources (issue 21); send `run.intake.digest` as
`envelope_decision.intake_digest` for "Approve and run unattended" and
"Approve, step by step". The stepwise verdict's words state the creation.
Frontend only; UX review against `.scratch/project-workflow/design.md`.

## Comments

### 2026-11-11: triage

Checked on master: `frontend/src` has no `intake_digest` and `EnvelopeSection.tsx`, `ChatPanel.tsx`, `chatStore` render no setup or intake rows. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `pypsa-gui/frontend/src/components/runMonitor/EnvelopeSection.tsx` (+ test), `components/ChatPanel.tsx`, `store/chatStore.ts` (+ tests). Frontend only.
- Reviewers: GK B (plan), UX (against `.scratch/project-workflow/design.md`). Send the UI plan to UX before code.

Acceptance:
- [x] The card's first row reads "Creates project <name> from the <template> template"; no digest shows in any row (UX ruling, #1332; the digest is only sent with the verdict).
- [x] The intake rows appear with their sources (issue 21); every row and default is shown, none folded (GK B F2).
- [x] "Approve and run unattended" and "Approve, step by step" send `envelope_decision.intake_digest` from `run.intake.digest`; a Vitest asserts the request body for both.
- [x] The step-by-step verdict's words state the creation.
- [x] A card without an intake keeps today's rows and sends no digest.

### 2026-11-11: reviewer plan verdicts

UX GO: no digest in any visible row (#1332); source lines in plain words; struck rows muted with "Struck"; a conflict row says "Not used: conflicts with <source>"; shots in Guided and Expert (struck, conflict, document-sourced, preset defaults, no-intake card).
GK B (46) GO under F1-F5: F1 the digest echoed is the one of the rendered view; F2 every bound row and default is on the card, nothing folded (#1283); F3 missing inputs listed and approve blocked with the reason; F4 fixture from the backend's own `card_view` shape (#1060), no digest on a deny verdict or a card without setup/intake; F5 a refused digest shows its reason.
Conflict: UX asked to fold after 5 rows, GK B F2 forbids folding; sent to the coordinator as options, built unfolded meanwhile.

