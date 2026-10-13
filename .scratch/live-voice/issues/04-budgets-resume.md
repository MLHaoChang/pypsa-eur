# Voice budgets, observability and long-job resume

Status: ready-for-agent
Type: task
Blocked by: 01, 02, 03, 09, 11

Implement steps 4 and 5 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Account separately for LiveKit/media, hosted STT/TTS and model usage using their
actual plans/billing units. Count duration/final usage once, reserve minimum costs, bound
time and dollars, close idle/abandoned sessions and persist backend jobs separately.
Restore verified context on resume without replaying effects. Instrument playback
interruption and useful-response latency; cache stable context with freshness guards.
Speech failures visibly fall back to typed input in the same harness session;
never replay a write or approval merely to restore audio.
Persist parent budgets across episodes/compaction/restart/profile changes. Measure
grounded-answer latency including endpointing/STT/tools/TTS, with acknowledgment
separate. Resume reconciles effects and refreshes credentials/generation without
restoring pending approval tokens.

Done when cost/error/reconnect/long-job gates pass with measured latency evidence.
See [spec](../spec.md).

## Comments
