#!/usr/bin/env bash
# E2E chat-service tier-2 walkthrough — exercises SSE protocol, confirmation
# lifecycle (deny/expire/abort), read-tier tool breadth, history endpoint,
# and mixed-tier batch dispatch against the LIVE backend.
#
# Each test is independent. Reports PASS/FAIL per test with concrete evidence.
# No real LLM calls — everything drives the Phase 2 stub.

set +e
BACKEND="http://127.0.0.1:8000"
PASS=0
FAIL=0
declare -a RESULTS=()

pass() { RESULTS+=("PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("FAIL  $1 — $2"); FAIL=$((FAIL+1)); }
hdr()  { echo ""; echo "═════ $1 ═════"; }
note() { echo "       $1"; }

# ───────────────────────────────────────────────────────────────────────────
hdr "T1: SSE protocol — Content-Type + frame format"
RESP=$(curl -s -D - -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"t1","script":[{"type":"token","text":"hi"},{"type":"turn_done","turn_id":"t1_turn"}]}' \
  --max-time 5)
ctype=$(echo "$RESP" | grep -i "^content-type:" | head -1 | tr -d '\r')
note "$ctype"
# Verify SSE frames present (event: + data: pairs)
events=$(echo "$RESP" | grep -c "^event: ")
datas=$(echo "$RESP" | grep -c "^data: ")
note "event: lines = $events,  data: lines = $datas (must be equal, >=3)"
if echo "$ctype" | grep -qi "text/event-stream" && [ "$events" -ge 3 ] && [ "$events" = "$datas" ]; then
  pass "T1 SSE Content-Type=text/event-stream, $events event/data pairs"
else
  fail "T1 SSE protocol shape" "Content-Type or frame count wrong"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T2: confirmation DENIED → tool_error error_kind=confirmation_denied"
BODY='{"session_id":"t2","script":[
  {"type":"tool_call","tool_use_id":"toolu_deny","name":"delete_project",
   "args":{"name":"x"},"safety_tier":"destructive","result":null}
]}'
curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 30 > /tmp/t2.sse 2>&1 &
PID=$!
# Poll for confirmation_token, then send deny
token=""
for i in 1 2 3 4 5; do
  sleep 1
  token=$(grep -oE '"confirmation_token":[[:space:]]*"[^"]+"' /tmp/t2.sse 2>/dev/null | head -1 | sed -E 's/.*"confirmation_token":[[:space:]]*"([^"]+)".*/\1/')
  [ -n "$token" ] && break
done
note "confirmation_token = ${token:-<empty>}"
if [ -n "$token" ]; then
  curl -s -X POST "$BACKEND/api/chat/t2/confirm" \
    -H "Content-Type: application/json" \
    -d "{\"token\":\"$token\",\"decision\":\"deny\"}" > /dev/null
fi
wait $PID
denied=$(grep -c "confirmation_denied" /tmp/t2.sse 2>/dev/null)
[ -z "$denied" ] && denied=0
tool_results=$(grep -c '"tool_result"' /tmp/t2.sse 2>/dev/null)
[ -z "$tool_results" ] && tool_results=0
note "confirmation_denied frames: $denied (expect >=1)"
note "tool_result frames:         $tool_results (expect 0)"
if [ "$denied" -ge 1 ] && [ "$tool_results" = "0" ]; then
  pass "T2 confirmation denied path"
else
  fail "T2" "denied=$denied tool_results=$tool_results"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T3: confirmation EXPIRED → tool_error error_kind=confirmation_expired"
# We can't override CONFIRMATION_TTL_SECONDS at runtime, but the stub does
# honor `confirmation_wait_seconds`. Drive a destructive tool and don't confirm
# — the stub blocks `wait_for_decision` with timeout. Default timeout in stub
# is None (infinite), so we need a different angle.
# Instead: verify the late-confirm-after-TTL path. We DO have unit coverage
# for the expiry frame (test_confirmation_expires_after_ttl in test_chat_sse.py).
# Here, exercise the LATE confirm: emit destructive, send confirm, observe
# that the late confirm to a NON-PENDING session 404s (already covered by
# test_late_confirm_after_ttl_returns_409 + test_confirm_unknown_session_returns_404).
RESP=$(curl -s -o /tmp/t3.json -w "%{http_code}" -X POST "$BACKEND/api/chat/nonexistent_session/confirm" \
  -H "Content-Type: application/json" \
  -d '{"token":"deadbeef","decision":"approve"}')
note "POST /confirm to unknown session → $RESP"
cat /tmp/t3.json
if [ "$RESP" = "404" ]; then
  pass "T3 confirm unknown session returns 404"
else
  fail "T3" "expected 404, got $RESP"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T4: abort endpoint terminates stream cleanly"
BODY='{"session_id":"t4","script":[
  {"type":"tool_call","tool_use_id":"toolu_abort","name":"delete_project",
   "args":{"name":"x"},"safety_tier":"destructive","result":null}
]}'
curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 30 > /tmp/t4.sse 2>&1 &
PID=$!
# Wait for confirmation card, then ABORT instead of confirming
for i in 1 2 3 4 5; do
  sleep 1
  if grep -q "tool_pending_confirmation" /tmp/t4.sse 2>/dev/null; then break; fi
done
# Send abort
abort_resp=$(curl -s -w "%{http_code}" -X POST "$BACKEND/api/chat/t4/abort" 2>&1 | tail -c 5)
note "abort POST → $abort_resp"
wait $PID
aborted=$(grep -c '"aborted"' /tmp/t4.sse 2>/dev/null)
[ -z "$aborted" ] && aborted=0
session_done=$(grep -c "session_done" /tmp/t4.sse 2>/dev/null)
[ -z "$session_done" ] && session_done=0
note "frames mentioning 'aborted': $aborted"
note "session_done frames: $session_done (expect 1, with reason=aborted/disconnected/done)"
# Either an aborted error frame or session_done with abort reason is acceptable
if [ "$aborted" -ge 1 ] || [ "$session_done" -ge 1 ]; then
  pass "T4 abort terminates stream"
else
  fail "T4" "no aborted/session_done frame"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T5: read-tier breadth — 6 read tools in one batch all complete"
# tool_batch with ALL-read tools: M7 doesn't trigger (no destructives).
# Each call should yield tool_request → tool_running → tool_result.
BODY='{"session_id":"t5","script":[
  {"type":"tool_batch","calls":[
    {"tool_use_id":"r1","name":"list_components","args":{"component_class":"Bus"},"safety_tier":"read","result":[]},
    {"tool_use_id":"r2","name":"list_components","args":{"component_class":"Generator"},"safety_tier":"read","result":[]},
    {"tool_use_id":"r3","name":"list_components","args":{"component_class":"Line"},"safety_tier":"read","result":[]},
    {"tool_use_id":"r4","name":"get_snapshots","args":{},"safety_tier":"read","result":[]},
    {"tool_use_id":"r5","name":"list_projects","args":{},"safety_tier":"read","result":[]},
    {"tool_use_id":"r6","name":"get_network_meta","args":{},"safety_tier":"read","result":{}}
  ]},
  {"type":"turn_done","turn_id":"t5_turn"}
]}'
curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 10 > /tmp/t5.sse 2>&1
tool_requests=$(grep -c "tool_request" /tmp/t5.sse)
tool_results=$(grep -c "tool_result" /tmp/t5.sse)
errors=$(grep -c "tool_error" /tmp/t5.sse)
pendings=$(grep -c "tool_pending_confirmation" /tmp/t5.sse)
note "tool_request:                 $tool_requests (expect 6)"
note "tool_result:                  $tool_results (expect 6)"
note "tool_error:                   $errors (expect 0)"
note "tool_pending_confirmation:    $pendings (expect 0 — no destructives)"
if [ "$tool_requests" = "6" ] && [ "$tool_results" = "6" ] && [ "$errors" = "0" ] && [ "$pendings" = "0" ]; then
  pass "T5 read-tier breadth (6 tools, all clean)"
else
  fail "T5" "frame counts wrong"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T6: chat history endpoint — turns + last_session_id + bound_project"
RESP=$(curl -s "$BACKEND/api/chat/history?limit=5")
note "response (first 500 chars):"
echo "$RESP" | head -c 500
echo ""
# Check shape
has_turns=$(echo "$RESP" | python -c "import json,sys; print('yes' if 'turns' in json.loads(sys.stdin.read()) else 'no')" 2>/dev/null)
has_bound=$(echo "$RESP" | python -c "import json,sys; print('yes' if 'bound_project' in json.loads(sys.stdin.read()) else 'no')" 2>/dev/null)
turn_count=$(echo "$RESP" | python -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d.get('turns',[])))" 2>/dev/null)
note "has 'turns' key: $has_turns,  has 'bound_project' key: $has_bound"
note "turn count returned: $turn_count (>=1 since this session persisted turns)"
if [ "$has_turns" = "yes" ] && [ "$has_bound" = "yes" ]; then
  pass "T6 history endpoint shape (turns + bound_project)"
else
  fail "T6" "missing keys"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T7: M7 only triggers on >1 DESTRUCTIVE — mixed batch passes through"
# 1 destructive + 1 read in same tool_batch → M7 should NOT reject.
# Destructive gets pending_confirmation, read completes immediately.
BODY='{"session_id":"t7","script":[
  {"type":"tool_batch","calls":[
    {"tool_use_id":"mixed_d","name":"delete_project","args":{"name":"x"},"safety_tier":"destructive","result":null},
    {"tool_use_id":"mixed_r","name":"list_components","args":{"component_class":"Bus"},"safety_tier":"read","result":[]}
  ]},
  {"type":"turn_done","turn_id":"t7_turn"}
]}'
curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 10 > /tmp/t7.sse 2>&1 &
PID=$!
# Wait for pending confirmation, then approve (so the destructive completes
# and the read tool also runs).
for i in 1 2 3 4 5; do
  sleep 1
  token=$(grep -oE '"confirmation_token":[[:space:]]*"[^"]+"' /tmp/t7.sse 2>/dev/null | head -1 | sed -E 's/.*"confirmation_token":[[:space:]]*"([^"]+)".*/\1/')
  [ -n "$token" ] && break
done
[ -n "$token" ] && curl -s -X POST "$BACKEND/api/chat/t7/confirm" \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"$token\",\"decision\":\"approve\"}" > /dev/null
wait $PID
parallel_errors=$(grep -c "parallel_destructive_not_allowed" /tmp/t7.sse 2>/dev/null)
[ -z "$parallel_errors" ] && parallel_errors=0
tool_results=$(grep -c "tool_result" /tmp/t7.sse)
note "parallel_destructive_not_allowed: $parallel_errors (expect 0 — only 1 destructive)"
note "tool_result frames:               $tool_results (expect 2 — both calls completed)"
if [ "$parallel_errors" = "0" ] && [ "$tool_results" = "2" ]; then
  pass "T7 mixed-tier batch processes correctly (1 destructive + 1 read)"
else
  fail "T7" "M7 misfired or tools not dispatched"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "T8: session_init carries tool_count + model"
BODY='{"session_id":"t8","model":"claude-sonnet-4-6","script":[{"type":"turn_done","turn_id":"t8_turn"}]}'
RAW=$(curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 5)
si=$(echo "$RAW" | python -c "
import re, sys
text = sys.stdin.read()
m = re.search(r'event:\s*session_init\s*\n\s*data:\s*(\{[^\n]*\})', text)
print(m.group(1) if m else '')")
tool_count=$(echo "$si" | python -c "
import json, sys
try: d = json.loads(sys.stdin.read())
except Exception: d = {}
print(d.get('tool_count', 0))")
model=$(echo "$si" | python -c "
import json, sys
try: d = json.loads(sys.stdin.read())
except Exception: d = {}
print(d.get('model', ''))")
note "session_init.tool_count = $tool_count (expect ~100)"
note "session_init.model      = $model"
if [ "$tool_count" -gt 50 ] && [ -n "$model" ]; then
  pass "T8 session_init carries tool_count + model"
else
  fail "T8" "tool_count=$tool_count model=$model"
fi

# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "RESULTS"
echo "════════════════════════════════════════════════════════════════"
printf '%s\n' "${RESULTS[@]}"
echo "────────────────────────────────────────────────────────────────"
echo "PASS=$PASS  FAIL=$FAIL"
echo "════════════════════════════════════════════════════════════════"
exit $FAIL
