# Move a maintenance window

Status: needs-triage
Type: task
Blocked by: 04 (#576)
Gated: yes (apply path)
Model tier: opus

Follow-up from ticket 04, accepted by the gatekeeper on #576. Read [ticket 04](04-chat-refinement.md) first.

## Gap

Ticket 04's example "move the maintenance window two weeks later" has no op. `shift` rolls the whole year.

## What to build

An availability op that moves one recorded outage window (from the availability generator's parameters, ticket 02) by a duration, re-generating rather than post-transforming. A series without such a window refuses it.

## Done when

On an availability series with a planned outage, "two weeks later" moves only that outage, and the rest of the series is unchanged.
