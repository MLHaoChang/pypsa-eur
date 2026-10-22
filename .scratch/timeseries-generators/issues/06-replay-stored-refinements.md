# Re-generating replays the stored refinements

Status: needs-triage
Type: task
Blocked by: 04 (#576)
Gated: yes (apply path)
Model tier: opus

Follow-up from ticket 04, accepted by the gatekeeper on #576. Read [ticket 04](04-chat-refinement.md) first.

## Gap

Ticket 04 says "re-running the generator replays the ops". Today the generate routes replay only when the caller passes `refinements` in the body, and the `generate_*` chat tools take no `refinements` argument.

## What to build

When a target's stored series carries `refinements`, a re-generate for that target replays them by default (opt out explicitly). Replay warnings are shown on the card.

## Done when

Re-generating a refined load from the editor and from chat, without passing ops, yields the refined series, and a replay refusal names the op and its index.
