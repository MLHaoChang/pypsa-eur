# Shared harness delegation bridge

Status: ready-for-agent
Type: task
Blocked by: 08, 09 (integrated acceptance; bridge extraction can start earlier)

Implement steps 1 and 4's shared coordinator path in
[the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Start from typed requests independently of media. Use the shared admission/turn
pipeline, stable speech context, single turn
lease, bounded queue and generation-aware result projection. No raw browser
tool dispatch or duplicate authorization implementation. Preserve existing cards
and closed SSE events. Cover Claude/OpenAI parity, complete eligible-tool
reachability and interrupted corrections. One model plans; speech synthesis reads
its answer. Carry exact project/run/source/window/asset references for follow-ups.

Done when ordinary harness gates and transport-equivalence fixtures pass without
duplicate actions. See [spec](../spec.md).
Integrate issue 08's full inventory/discovery and issue 09's durable owner/thread
contract. The pending queue handles overlapping utterances; issue 11 must release
long-job turns and provide independent observation before workflow acceptance.

## Comments
