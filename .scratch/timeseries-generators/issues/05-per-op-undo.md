# Per-op undo of a refinement

Status: needs-triage
Type: task
Blocked by: 04 (#576)
Gated: yes (apply path), plus the UX reviewer for the UI
Model tier: opus

Follow-up from ticket 04, accepted by the gatekeeper on #576. Read [ticket 04](04-chat-refinement.md) first.

## Gap

Ticket 04 says "every op shows in the change log and can be undone". Today undo is the apply's whole snapshot. A refined artifact records its ordered op list and `based_on`, but nothing steps back one op.

## What to build

Undoing op *k* re-refines from the `based_on` artifact with the op list minus op *k*, producing a new pending artifact and its old→new card. The change log lists each op with its parameters, not only its name.

## Done when

On a series refined by two ops, undoing the second shows a card whose "new" equals the series after the first op alone, and accepting it applies that series.
