# One load-factor basis

Status: needs-triage
Type: task
Blocked by: 04 (#576)
Gated: no
Model tier: sonnet

Follow-up from ticket 04, accepted by the gatekeeper on #576. Read [ticket 04](04-chat-refinement.md) first.

## Gap

There are two load factors. The refine stats use hourly mean ÷ peak over the source year; the edit card uses weighted energy ÷ (peak × hours) over the model's snapshots. Since ts-05 each is labelled with its basis (`load_factor_basis` in the stats, "(weighted, model snapshots)" on the card), but a user who sees both can still read two different numbers.

## What to build

Report the card's basis in the refine stats as well, aligned and weighted like the card, keeping the source-year figure only if a consumer needs it.

## Done when

The refine answer and the card show the same load factor for the same series.
