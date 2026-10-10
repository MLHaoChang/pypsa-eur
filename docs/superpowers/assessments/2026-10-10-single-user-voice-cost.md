<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Hosted voice cost for one user

Date checked: 2026-10-10. Assumption: up to 10 conversation hours per month,
browser audio, one concurrent user, existing application/backend hosting.
These are price-based planning estimates, not measured harness usage or a quote.

## Recommended transport tier

[LiveKit Cloud pricing](https://livekit.com/pricing.md) lists Build at $0/month,
Ship starting at $50/month, and Scale starting at $500/month. Build includes
1,000 hosted-agent session minutes, five concurrent agent sessions, one agent
deployment, 5,000 WebRTC minutes, 50 GB downstream transfer and 100,000
observability events. Ten conversation hours are 600 session minutes. Even
budgeting two connected participants as 1,200 WebRTC minutes fits that allowance;
audio-only traffic should be well below 50 GB, but verify actual accounting.

Use Build initially, with actual quotas/resources tested. Account for testing,
connected idle time and abandoned sessions as well as conversation. No automatic
paid upgrade. End inactive voice sessions while backend jobs continue.
Build's $2.50 LiveKit Inference credits do not pay direct OpenAI/Anthropic/Deepgram
bills. Start with existing provider credentials and the shared harness.

Build lacks hosted-agent cold-start prevention; Ship includes it plus team
features/rollback/email support. Measure first-session startup before accepting
the responsiveness gate. Paid hosted-agent always-on deployment is an option
only if the measured cold starts justify the additional $50 base charge.
Media transport and agent hosting are distinct from deployment of our application
backend and owner-pinned authenticated bridge.

## Separate speech and reasoning charges

[Deepgram pricing](https://deepgram.com/pricing): current pay-as-you-go Nova-3
Multilingual streaming $0.0058/minute (promotional; regular $0.0092); Flux
Multilingual $0.0078/minute. Conservatively budget the full 600-minute recognition
connection: $3.48 current Nova, $5.52 regular Nova, or $4.68 Flux. Final selection
depends on required languages/numbers/device/end-of-turn accuracy.

Speech synthesis depends on the chosen model and amount actually generated,
including interrupted audio. Allocate about $5–10 for a first 10-hour pilot if
the assistant speaks roughly half the time. This is a provisional allowance:
for example, direct Deepgram Aura-2 costs $0.030/1,000 characters. At an assumed
150 words/minute and six characters per word including spaces, 300 generated
minutes would use 270,000 characters and cost $8.10. Language, speaking speed and
provider matter. This is not a vendor change or a quote for our OpenAI adapter.

Current [OpenAI pricing](https://developers.openai.com/api/docs/pricing.md) lists
gpt-4o-mini-tts at $0.60/M text-input tokens and $12/M output-audio tokens.
The [deprecation schedule](https://developers.openai.com/api/docs/deprecations)
now schedules legacy standalone TTS shutdown on January 6, 2027 and recommends
gpt-realtime-2.1-mini. Refresh the hosted TTS implementation choice, exact API
contract/pricing and shared-answer fidelity before production; do not base a new
long-lived implementation on a retiring model. Keep vendor adapters replaceable.

[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing):
Sonnet 4.6 costs $3/M uncached input and $15/M output tokens. Illustrative totals,
not predictions from conversation duration: 1M input + 100k output = $4.50;
10M input + 500k output = $37.50. Cached-input pricing can reduce repeated-prefix
cost, with cache-write charges accounted separately. Tool schemas/history/tool
results and repeated model rounds count; extensive engineering experiments can
cost more than simple conversation. Preserve configured profiles; no silent switch.

At the illustrative lower token volume, direct inference/speech is roughly
$13–20 for ten hours. At the higher token volume, roughly $46–53, before tax,
application hosting or simulation compute. This is not a spend ceiling. Set an
explicit pilot budget and track dollar/duration/token units cumulatively; do not
convert conversation hours into a guaranteed flat LLM price.

## Implementation-plan implication

Start with the free Build tier and direct API credentials for the one-user pilot.
Retain the [reviewed shared-harness architecture](2026-10-10-hosted-assistant-architecture-decision.md)
and [release gates](../plans/2026-10-10-live-voice-conversation.md). Add Build
quota/cold-start checks and current standalone-TTS deprecation/migration validation
to the hosted profile/account proof. No subscriptions or paid API tests were
started for this assessment.
