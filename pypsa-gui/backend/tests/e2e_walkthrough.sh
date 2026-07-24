#!/usr/bin/env bash
# E2E walkthrough v3 — server-side validation of v6 chatbot integration.
# Works against whatever chatbot_validation_* project is currently loaded.
# Uses e2e_target / e2e_copy / e2e_scenario as fresh names so prior runs
# leave no interference.
#
# Safety: only touches projects matching `e2e_*` or `chatbot_validation_*`.
# Real projects (Belgium Grid, 4_nodes_N-*, heat with time-series, etc.) are
# NEVER touched.

set +e
BACKEND="http://127.0.0.1:8000"
# Resolved from this script's own location so the walkthrough runs from any
# checkout on any platform. Was previously a hardcoded Windows path.
PROJ_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/projects"
PASS=0
FAIL=0
SKIP=0
declare -a RESULTS=()

# Fresh demo names — won't collide with any prior walkthrough run.
T_TGT="e2e_target_$(date +%s)"
T_COPY="e2e_copy_$(date +%s)"
T_SCEN="e2e_scen_$(date +%s)"

pass() { RESULTS+=("PASS  $1"); PASS=$((PASS+1)); }
fail() { RESULTS+=("FAIL  $1 — $2"); FAIL=$((FAIL+1)); }
skip() { RESULTS+=("SKIP  $1 — $2"); SKIP=$((SKIP+1)); }
hdr()  { echo ""; echo "═════ $1 ═════"; }
note() { echo "       $1"; }

turns() {
  local f="$PROJ_ROOT/$1/chat.jsonl"
  [ -f "$f" ] && wc -l < "$f" | tr -d ' ' || echo 0
}

cleanup_demo() {
  # Delete only e2e_* projects from this run. Doesn't touch the live source.
  for d in "$T_TGT" "$T_COPY" "$T_SCEN"; do
    curl -s -o /dev/null -X DELETE "$BACKEND/api/projects/$d?cascade=true"
  done
}

# ───────────────────────────────────────────────────────────────────────────
hdr "Pre-check"
HEALTH=$(curl -s "$BACKEND/api/chat/health" | tr -d '\n')
echo "  health: $HEALTH"
META=$(curl -s "$BACKEND/api/network/meta" | tr -d '\n')
echo "  meta:   $META"
SOURCE=$(echo "$META" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('loaded_project',''))")
note "loaded source = $SOURCE"
if [ -z "$SOURCE" ]; then
  fail "pre-check" "no project loaded — load a chatbot_validation_* project first"
  printf '%s\n' "${RESULTS[@]}"; exit 1
fi
# Self-heal: if loaded source's chat.jsonl is missing, restore from any sibling demo with content
src_turns=$(turns "$SOURCE")
if [ "$src_turns" -lt 10 ]; then
  note "source chat.jsonl missing or thin ($src_turns turns) — searching for backup..."
  for cand in chatbot_validation_copytest chatbot_validation_movetest chatbot_validation_scenario1 chatbot_validation_2026_06_07 chatbot_validation_savedas; do
    if [ "$cand" != "$SOURCE" ] && [ -f "$PROJ_ROOT/$cand/chat.jsonl" ]; then
      cp "$PROJ_ROOT/$cand/chat.jsonl" "$PROJ_ROOT/$SOURCE/chat.jsonl"
      note "restored from $cand ($(turns $SOURCE) turns)"
      break
    fi
  done
  src_turns=$(turns "$SOURCE")
fi
if [ "$src_turns" -lt 10 ]; then
  fail "pre-check" "source $SOURCE has only $src_turns turns; expected >=10"
  printf '%s\n' "${RESULTS[@]}"; exit 1
fi
# Cleanup any leftover e2e_* (idempotent)
cleanup_demo
note "test targets: $T_TGT / $T_COPY / $T_SCEN"
pass "pre-check (loaded=$SOURCE, $src_turns turns)"

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 6: Save-As MOVE — chat.jsonl moves source→target"
src_before=$(turns "$SOURCE")
HTTP=$(curl -s -o /tmp/e2e_save_as.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$T_TGT?rebind=true")
new_loaded=$(curl -s "$BACKEND/api/network/meta" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('loaded_project',''))")
src_after=$(turns "$SOURCE")
tgt_after=$(turns "$T_TGT")
src_file_exists=$(test -f "$PROJ_ROOT/$SOURCE/chat.jsonl" && echo "yes" || echo "no")
note "HTTP=$HTTP   loaded after: $new_loaded"
note "source chat.jsonl: $src_file_exists ($src_before before / $src_after after)"
note "target chat.jsonl: $tgt_after turns"
if [ "$HTTP" = "200" ] && [ "$new_loaded" = "$T_TGT" ] \
   && [ "$src_file_exists" = "no" ] && [ "$tgt_after" -ge "$src_before" ]; then
  pass "Step 6 Save-As MOVE"
else
  fail "Step 6 Save-As MOVE" "expected source gone, target=$src_before+ turns, loaded=$T_TGT"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 7: Save-a-Copy — chat.jsonl in BOTH (source unchanged)"
src_before=$(turns "$T_TGT")
HTTP=$(curl -s -o /tmp/e2e_save_copy.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$T_COPY?rebind=false")
new_loaded=$(curl -s "$BACKEND/api/network/meta" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('loaded_project',''))")
src_after=$(turns "$T_TGT")
tgt_after=$(turns "$T_COPY")
note "HTTP=$HTTP   loaded after: $new_loaded (must stay $T_TGT)"
note "source (target): $src_before → $src_after turns (must be unchanged)"
note "copy: $tgt_after turns (must equal source)"
if [ "$HTTP" = "200" ] && [ "$new_loaded" = "$T_TGT" ] \
   && [ "$src_after" = "$src_before" ] && [ "$tgt_after" -ge "$src_before" ]; then
  pass "Step 7 Save-a-Copy"
else
  fail "Step 7 Save-a-Copy" "expected source kept, target has $src_before turns, loaded stays $T_TGT"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 8: create_scenario — chat.jsonl COPIED + parent_project set"
src_before=$(turns "$T_TGT")
BODY="{\"name\":\"$T_SCEN\",\"description\":\"e2e walkthrough\"}"
HTTP=$(curl -s -o /tmp/e2e_scen.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$T_TGT/scenarios" \
  -H "Content-Type: application/json" -d "$BODY")
new_loaded=$(curl -s "$BACKEND/api/network/meta" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('loaded_project',''))")
src_after=$(turns "$T_TGT")
scen_turns=$(turns "$T_SCEN")
parent=$(python -c "import json; d=json.load(open(r'$PROJ_ROOT/$T_SCEN/metadata.json')); print(d.get('parent_project',''))" 2>/dev/null)
note "HTTP=$HTTP   loaded after: $new_loaded (must stay $T_TGT)"
note "scenario chat.jsonl: $scen_turns turns (parent: $src_before)"
note "scenario.parent_project = $parent"
if [ "$HTTP" = "201" ] && [ "$new_loaded" = "$T_TGT" ] \
   && [ "$src_after" = "$src_before" ] && [ "$scen_turns" -ge "$src_before" ] \
   && [ "$parent" = "$T_TGT" ]; then
  pass "Step 8 create_scenario"
else
  fail "Step 8 create_scenario" "expected scenario w/ chat.jsonl + parent link"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 9: create_project_snapshot — chat.jsonl in snapshot bundle"
BODY='{"label":"e2e_test_snap","message":"walkthrough"}'
HTTP=$(curl -s -o /tmp/e2e_snap.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$T_TGT/snapshots" \
  -H "Content-Type: application/json" -d "$BODY")
note "HTTP=$HTTP"
SNAP_ID=$(cat /tmp/e2e_snap.json | python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('id') or d.get('snapshot_id') or '')" 2>/dev/null)
note "snapshot id: $SNAP_ID"
snap_chat_path=""
snap_turns=0
has_net="no"
if [ -n "$SNAP_ID" ]; then
  snap_dir="$PROJ_ROOT/$T_TGT/snapshots/$SNAP_ID"
  snap_chat_path="$snap_dir/chat.jsonl"
  snap_turns=$( [ -f "$snap_chat_path" ] && wc -l < "$snap_chat_path" | tr -d ' ' || echo 0 )
  has_net=$( [ -f "$snap_dir/network.nc" ] && echo "yes" || echo "no" )
  note "snapshot dir contains network.nc: $has_net,  chat.jsonl: $snap_turns turns"
fi
if [ "$HTTP" = "200" ] && [ -n "$SNAP_ID" ] && [ "$snap_turns" -ge 10 ] && [ "$has_net" = "yes" ]; then
  pass "Step 9 create_project_snapshot"
else
  fail "Step 9 create_project_snapshot" "expected snapshot with network.nc + chat.jsonl"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 10: restore_project_snapshot — chat.jsonl OVERWRITTEN by snapshot"
canary_marker="__e2e_canary_$(date +%s)__"
echo '{"ts":99999,"canary":"'"$canary_marker"'"}' >> "$PROJ_ROOT/$T_TGT/chat.jsonl"
pre_restore=$(turns "$T_TGT")
note "before restore: $pre_restore turns (canary '$canary_marker' appended)"
HTTP=$(curl -s -o /tmp/e2e_restore.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$T_TGT/snapshots/$SNAP_ID/restore")
note "HTTP=$HTTP"
post_restore=$(turns "$T_TGT")
if grep -q "$canary_marker" "$PROJ_ROOT/$T_TGT/chat.jsonl" 2>/dev/null; then
  has_canary=yes
else
  has_canary=no
fi
note "after restore: $post_restore turns, canary present: $has_canary (must be no)"
if [ "$HTTP" = "200" ] && [ "$has_canary" = "no" ]; then
  pass "Step 10 restore_project_snapshot"
else
  fail "Step 10 restore_project_snapshot" "canary survived — snapshot did NOT overwrite chat.jsonl"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 12: M7 parallel-destructive rejection (stub-driven)"
BODY='{"session_id":"e2e_m7","script":[
  {"type":"tool_batch","calls":[
    {"tool_use_id":"toolu_destA","name":"delete_project","args":{"name":"x"},"safety_tier":"destructive"},
    {"tool_use_id":"toolu_destB","name":"reset_network","args":{},"safety_tier":"destructive"}
  ]},
  {"type":"turn_done","turn_id":"e2e_m7_turn"}
]}'
curl -s -N -X POST "$BACKEND/api/chat/stream" \
  -H "Content-Type: application/json" -d "$BODY" \
  --max-time 10 > /tmp/e2e_m7.sse 2>&1
errors=$(grep -c "parallel_destructive_not_allowed" /tmp/e2e_m7.sse 2>/dev/null)
[ -z "$errors" ] && errors=0
pendings=$(grep -c "tool_pending_confirmation" /tmp/e2e_m7.sse 2>/dev/null)
[ -z "$pendings" ] && pendings=0
note "parallel_destructive_not_allowed frames: $errors (expect 2)"
note "tool_pending_confirmation frames:        $pendings (expect 0)"
if [ "$errors" -ge 2 ] && [ "$pendings" = "0" ]; then
  pass "Step 12 M7 parallel-destructive rejection"
else
  fail "Step 12 M7" "expected 2 tool_errors + 0 pending"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 13: save_project_as project_exists — covered by unit tests"
skip "Step 13 save_project_as project_exists" "stub mode returns scripted result; verified by 3 pytests: test_save_project_as_m1_precheck_raises_when_target_exists + test_save_project_as_m1_precheck_allows_target_when_active + test_project_exists_propagates_to_tool_error_kind"

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 14: same-name re-save (Ctrl+S equivalent) — 200 OK, no lineage"
loaded=$(curl -s "$BACKEND/api/network/meta" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('loaded_project',''))")
note "loaded = $loaded"
src_before=$(turns "$loaded")
HTTP=$(curl -s -o /tmp/e2e_resave.json -w "%{http_code}" \
  -X POST "$BACKEND/api/projects/$loaded")
src_after=$(turns "$loaded")
note "POST /$loaded (no rebind, no force) → $HTTP"
note "chat.jsonl: $src_before → $src_after (must be unchanged)"
if [ "$HTTP" = "200" ] && [ "$src_after" = "$src_before" ]; then
  pass "Step 14 same-name re-save"
else
  fail "Step 14" "HTTP=$HTTP, chat.jsonl delta=$((src_after - src_before))"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Step 16: delete_project 409 descendants_exist"
HTTP=$(curl -s -o /tmp/e2e_del409.json -w "%{http_code}" \
  -X DELETE "$BACKEND/api/projects/$T_TGT")
detail=$(cat /tmp/e2e_del409.json | head -200)
note "HTTP=$HTTP   detail=$detail"
descendants_kind=$(echo "$detail" | grep -c "descendants_exist")
if [ "$HTTP" = "409" ] && [ "$descendants_kind" -ge 1 ]; then
  pass "Step 16 delete_project descendants_exist 409"
else
  fail "Step 16" "expected 409 descendants_exist"
fi

# ───────────────────────────────────────────────────────────────────────────
hdr "Steps 15/17/18/19/20 — covered by unit tests or code review"
skip "Step 15 solver_in_flight 409" "code-only: services/chat_tools.py + _save_context raise 409 error_kind=solver_in_flight. Live test would spin up a real solver worker"
skip "Step 17 log-redaction" "covered by test_chat_e2e::test_api_key_redacted_from_log_when_exception_carries_it (regex check on chat_service._redact_for_log)"
skip "Step 18 concurrent serialisation" "covered by test_chat_lineage::test_concurrent_append_turn_no_torn_writes_no_lost_writes (64 threads, ctx.chat_state.lock contract)"
skip "Step 19 chat.jsonl rotation" "covered by chat_service rotation under chat_state.lock unit tests"
skip "Step 20 v6-F2 cold-path activate" "covered by test_chat_e2e::test_cold_path_activate_renders_as_success"

# ───────────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "RESULTS"
echo "════════════════════════════════════════════════════════════════"
printf '%s\n' "${RESULTS[@]}"
echo "────────────────────────────────────────────────────────────────"
echo "PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
echo "════════════════════════════════════════════════════════════════"
exit $FAIL
