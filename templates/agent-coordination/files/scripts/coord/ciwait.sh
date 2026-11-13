#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Wait for CI on PRs that have no session (the coordinator's own PRs). A PR
# with a session is driven by that session, which reports `premerge ok`.
#
#   scripts/coord/ciwait.sh <max-minutes, at most 118> <pr>...
#
# Run it with the Bash tool's run_in_background, never with `&`: a detached
# command's completion notice never arrives.
#
# Board mode watches every `prs` row of the board instead of a PR list:
#
#   scripts/coord/ciwait.sh --board <board.json> [<max-minutes, at most 118>]
#
# It prints one READY/FAIL/MACOS line per PR head and keeps watching the rest, so a
# single process covers every coordinator-driven PR and is the one thing to restart
# after a container restart. The board is re-read each round (a PR added later is
# picked up, one dropped is forgotten); a PR that gets a new head after a push is
# reported again. It prints TIMEOUT only at the limit. Run it with the Monitor tool
# (each line is one wake), not run_in_background, which wakes once at exit.
#
# Polls each PR's head every 240 s and exits on the first hit:
#   FAIL <pr> <name> <conclusion>  a completed run that is not success, skipped
#                                  or neutral (a cancelled "Integration (macos)"
#                                  is ignored)
#   READY <pr> <sha>               at least one run, and none pending
#   MACOS <pr> <sha>               only "Integration (macos)" is pending, its job
#                                  has no runner assigned (runner_id 0, as premerge
#                                  tests it; a queued job has none) and it is over
#                                  30 minutes old. A job with a runner is running,
#                                  however long it takes (about an hour is normal).
#                                  The check run's own status can lag the job, so
#                                  only the job's runner_id decides.
#   TIMEOUT                        nothing hit within the limit (board mode: the
#                                  limit passed; a PR line already printed stays)
# A head with no check run yet is not ready.
#
# Environment: PREMERGE_REMOTE (default origin), PREMERGE_REPO (default
# MLHaoChang/pypsa-eur), CIWAIT_INTERVAL (seconds, default 240).

set -euo pipefail

usage() { echo "usage: $0 <max-minutes<=118> <pr>...  |  $0 --board <board.json> [<max-minutes<=118>]" >&2; exit 2; }
board=""
if [[ ${1:-} == --board ]]; then
  (($# >= 2 && $# <= 3)) || usage
  board=$2; max=${3:-118}; [[ -f $board ]] || { echo "ciwait: no such board $board" >&2; exit 2; }
  [[ $max =~ ^[0-9]+$ ]] && ((max >= 1 && max <= 118)) || usage
  prs=()
else
  (($# >= 2)) && [[ $1 =~ ^[0-9]+$ ]] && (($1 >= 1 && $1 <= 118)) || usage
  max=$1; shift
  for pr in "$@"; do [[ $pr =~ ^[0-9]+$ ]] || usage; done
  prs=("$@")
fi
remote=${PREMERGE_REMOTE:-origin}
repo=${PREMERGE_REPO:-MLHaoChang/pypsa-eur}
interval=${CIWAIT_INTERVAL:-240}
deadline=$((SECONDS + max * 60))

board_prs() { # the board's open-PR numbers
  python3 -c 'import json,sys
for r in json.load(open(sys.argv[1]))["rows"]:
    if r.get("board") == "prs": print(r["pr"])' "$board" 2>/dev/null || true
}

# check <pr>: sets $hit to the hit line (FAIL/READY/MACOS ...) or "", and $sha
check() {
  local pr=$1 runs fail pending since mid
  hit=""
  sha=$(git ls-remote "$remote" "refs/pull/$pr/head" | cut -f1)
  [[ -n $sha ]] || return 0
  runs=$(gh api "repos/$repo/commits/$sha/check-runs?per_page=100" \
    --jq '.check_runs[] | [.status, (.conclusion // "-"), .name, (.started_at // ""), .id] | @tsv') || return 0
  [[ -n $runs ]] || return 0  # no runs yet: not ready
  fail=$(awk -F'\t' '$1 == "completed" && $2 != "success" && $2 != "skipped" && $2 != "neutral" \
    && !($2 == "cancelled" && $3 == "Integration (macos)") { print $3, $2; exit }' <<<"$runs")
  if [[ -n $fail ]]; then hit="FAIL $pr $fail"; return 0; fi
  pending=$(awk -F'\t' '$1 != "completed" { print $3 }' <<<"$runs")
  if [[ -z $pending ]]; then hit="READY $pr $sha"; return 0; fi
  if [[ $pending == "Integration (macos)" ]]; then
    IFS=$'\t' read -r since mid < <(awk -F'\t' '$3 == "Integration (macos)" { print $4 "\t" $5; exit }' <<<"$runs")
    if [[ -n $since ]] && (($(date +%s) - $(date -d "$since" +%s) > 1800)); then
      if [[ $(gh api "repos/$repo/actions/jobs/$mid" --jq '.runner_id // 0' 2>/dev/null) == 0 ]]; then
        hit="MACOS $pr $sha"
      fi
    fi
  fi
}

sha= hit=
declare -A seen=() # board mode: "<pr> <sha>" already reported
while ((SECONDS < deadline)); do
  [[ -n $board ]] && mapfile -t prs < <(board_prs)
  for pr in "${prs[@]}"; do
    check "$pr"
    [[ -n $hit ]] || continue
    if [[ -z $board ]]; then echo "$hit"; exit 0; fi
    [[ -z ${seen["$pr $sha"]:-} ]] || continue
    seen["$pr $sha"]=1
    echo "$hit"
  done
  sleep "$interval"
done
echo TIMEOUT
