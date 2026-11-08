<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 37 — Plan card renders the intake and sends its digest

Status: needs-triage
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
