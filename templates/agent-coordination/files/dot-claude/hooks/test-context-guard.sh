#!/bin/bash
# Tests for context-guard.sh. Run: .claude/hooks/test-context-guard.sh
here=$(cd "$(dirname "$0")" && pwd)
hook="$here/context-guard.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail=0
export HOME="$work/home" CONTEXT_GUARD_STATE_DIR="$work/state"
mkdir -p "$HOME" "$CONTEXT_GUARD_STATE_DIR"

# transcript <file> <input> <cache_read> <cache_creation>: one assistant line
# with that usage, after a user line and a sidechain line with a huge usage.
transcript() {
  {
    echo '{"type":"user","message":{"content":"hi"}}'
    echo '{"type":"assistant","isSidechain":true,"message":{"usage":{"input_tokens":900000}}}'
    printf '{"type":"assistant","message":{"usage":{"input_tokens":%s,"cache_read_input_tokens":%s,"cache_creation_input_tokens":%s}}}\n' "$2" "$3" "$4"
    echo '{"type":"user","message":{"content":"tool result"}}'
  } >"$1"
}

run() { # run <session> <transcript>
  jq -n --arg s "$1" --arg t "$2" '{session_id: $s, transcript_path: $t}' | "$hook"
}

check() { # check <name> <expected-substring|""> <actual output> <exit>
  local name=$1 want=$2 out=$3 code=$4
  if [[ $code -ne 0 ]]; then echo "FAIL: $name: exit $code"; fail=1
  elif [[ -z $want && -n $out ]]; then echo "FAIL: $name: expected silence, got: $out"; fail=1
  elif [[ -n $want && $out != *"$want"* ]]; then echo "FAIL: $name: expected '$want', got: $out"; fail=1; fi
}

t="$work/t.jsonl"
go() { out=$(run "$@"); code=$?; }

transcript "$t" 10 100000 50000            # 150010: below 200k
go s1 "$t"; check "below limit is silent" "" "$out" $code

transcript "$t" 10 150000 50000            # 200010
go s1 "$t"; check "at limit warns" "Context at 200k (limit 200k): write your hand-off note (steward Rotation) and send it to the coordinator now." "$out" $code
jq -e '.hookSpecificOutput.hookEventName == "PostToolUse"' <<<"$out" >/dev/null || { echo "FAIL: output shape"; fail=1; }

go s1 "$t"; check "same level is silent" "" "$out" $code
transcript "$t" 10 150000 59000            # 209010: +9k
go s1 "$t"; check "+9k is silent" "" "$out" $code
transcript "$t" 10 150000 70000            # 220010: +20k
go s1 "$t"; check "+20k warns again" "Context at 220k" "$out" $code
go s2 "$t"; check "another session warns on its own" "Context at 220k" "$out" $code

: >"$HOME/.claude-coordinator"
transcript "$t" 10 100000 55000            # 155010
go s3 "$t"; check "coordinator limit is 150k" "Context at 155k (limit 150k)" "$out" $code
rm "$HOME/.claude-coordinator"

# Fail open.
go s4 "$work/missing.jsonl"; check "missing transcript" "" "$out" $code
echo 'not json' >"$work/bad.jsonl"
go s4 "$work/bad.jsonl"; check "garbage transcript" "" "$out" $code
out=$(echo 'garbage' | "$hook"); code=$?; check "garbage stdin" "" "$out" $code
out=$(echo '{}' | "$hook"); code=$?; check "no transcript_path" "" "$out" $code

# A transcript larger than the tail window still finds the newest usage.
filler='{"type":"user","message":{"content":"'"$(head -c 300 /dev/zero | tr '\0' x)"'"}}'
{ for _ in $(seq 1 3000); do echo "$filler"; done
  echo '{"type":"assistant","message":{"usage":{"input_tokens":1,"cache_read_input_tokens":249999}}}'; } >"$work/big.jsonl"
go s5 "$work/big.jsonl"; check "big transcript" "Context at 250k" "$out" $code

((fail)) && exit 1
echo "all passed"
