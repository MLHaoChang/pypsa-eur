# Peak as a share of the connection

Status: needs-triage
Type: task
Blocked by: 04 (#576)
Gated: no
Model tier: sonnet

Follow-up from ticket 04, accepted by the gatekeeper on #576. Read [ticket 04](04-chat-refinement.md) first.

## Gap

Ticket 04's preview lists "peak as % of nameplate/connection", and the owner's sentence ("it reaches 80 %") presumes the model can read it. Neither the refine stats nor the card report it.

## What to build

When the project has a point-of-connection link, the refine stats and the preview's summary report the aligned peak as a share of its `p_nom`, labelled with the link's display name.

## Done when

A load at 80 MW under a 100 MW connection reads "80 % of the grid connection" in the preview and in the refine stats.
