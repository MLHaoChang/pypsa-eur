#!/bin/bash
# PostToolUse hook: tells the agent to hand off once its context passes the
# rotation limit (200k tokens; 300k in the coordinator session, which creates
# ~/.claude-coordinator at start), then again at every further +20k.
# Reads the latest assistant `usage` from the transcript. Silent and fast
# below the limit; fails open (no output, exit 0) on any error.
# Test overrides: CONTEXT_GUARD_LIMIT (tokens), CONTEXT_GUARD_STATE_DIR.
trap 'exit 0' ERR
exec 2>/dev/null
command -v jq >/dev/null || exit 0

input=$(cat)
transcript=$(jq -r '.transcript_path // empty' <<<"$input") || exit 0
[[ -r $transcript ]] || exit 0
session=$(jq -r '.session_id // "x"' <<<"$input" | tr -c 'A-Za-z0-9_-' '_')

if [[ -n ${CONTEXT_GUARD_LIMIT:-} ]]; then limit=$CONTEXT_GUARD_LIMIT
elif [[ -e $HOME/.claude-coordinator ]]; then limit=300000
else limit=200000; fi

# Only the tail: the newest assistant line is near the end. The first line of
# the window may be cut, so it is dropped.
tokens=$(tail -c 400000 "$transcript" | tail -n +2 | jq -Rr '
  fromjson? | select(.type == "assistant" and .isSidechain != true and .message.usage != null)
  | .message.usage
  | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)' |
  tail -n 1)
[[ $tokens =~ ^[0-9]+$ ]] || exit 0
((tokens >= limit)) || exit 0

state_dir=${CONTEXT_GUARD_STATE_DIR:-${TMPDIR:-/tmp}}
state="$state_dir/context-guard-$session"
last=$(cat "$state" 2>/dev/null || true)
[[ $last =~ ^[0-9]+$ ]] || last=0
((last == 0 || tokens >= last + 20000)) || exit 0
echo "$tokens" >"$state" 2>/dev/null || true

msg="Context at $((tokens / 1000))k (limit $((limit / 1000))k): write your hand-off note (steward Rotation) and send it to the coordinator now."
jq -n --arg m "$msg" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $m}}'
exit 0
