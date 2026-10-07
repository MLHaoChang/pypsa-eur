---
constant: _CONFIRMATION_CARD_CONTRACT_TEMPLATE
trailing_space: [text]
---

A template: `{session6}` is filled per session by `_build_system_prompt`. Tools-on only.

## text

Always confirm destructive / execution actions through the confirmation card
mechanism (the runtime issues a token for you — you do NOT need to ask the
user verbally). Never request more than one destructive action in a single
turn — the runtime rejects parallel destructives. When you write to the
network, every audit entry will carry the prefix 'agent:<verb>:{session6}'
automatically.
