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
# Polls each PR's head every 240 s and exits on the first hit:
#   FAIL <pr> <name> <conclusion>  a completed run that is not success, skipped
#                                  or neutral (a cancelled "Integration (macos)"
#                                  is ignored)
#   READY <pr> <sha>               at least one run, and none pending
#   MACOS <pr> <sha>               only "Integration (macos)" is pending, and it
#                                  has been queued for over 30 minutes
#   TIMEOUT                        nothing hit within the limit
# A head with no check run yet is not ready.
#
# Environment: PREMERGE_REMOTE (default origin), PREMERGE_REPO (default
# MLHaoChang/pypsa-eur), CIWAIT_INTERVAL (seconds, default 240).

set -euo pipefail

usage() { echo "usage: $0 <max-minutes<=118> <pr>..." >&2; exit 2; }
(($# >= 2)) && [[ $1 =~ ^[0-9]+$ ]] && (($1 >= 1 && $1 <= 118)) || usage
max=$1; shift
for pr in "$@"; do [[ $pr =~ ^[0-9]+$ ]] || usage; done
remote=${PREMERGE_REMOTE:-origin}
repo=${PREMERGE_REPO:-MLHaoChang/pypsa-eur}
interval=${CIWAIT_INTERVAL:-240}
deadline=$((SECONDS + max * 60))

while ((SECONDS < deadline)); do
  for pr in "$@"; do
    sha=$(git ls-remote "$remote" "refs/pull/$pr/head" | cut -f1)
    [[ -n $sha ]] || continue
    runs=$(gh api "repos/$repo/commits/$sha/check-runs?per_page=100" \
      --jq '.check_runs[] | [.status, (.conclusion // "-"), .name, (.started_at // .created_at // "")] | @tsv') || continue
    [[ -n $runs ]] || continue  # no runs yet: not ready
    fail=$(awk -F'\t' '$1 == "completed" && $2 != "success" && $2 != "skipped" && $2 != "neutral" \
      && !($2 == "cancelled" && $3 == "Integration (macos)") { print $3, $2; exit }' <<<"$runs")
    if [[ -n $fail ]]; then echo "FAIL $pr $fail"; exit 0; fi
    pending=$(awk -F'\t' '$1 != "completed" { print $3 }' <<<"$runs")
    if [[ -z $pending ]]; then echo "READY $pr $sha"; exit 0; fi
    if [[ $pending == "Integration (macos)" ]]; then
      queued=$(awk -F'\t' '$3 == "Integration (macos)" { print $4; exit }' <<<"$runs")
      if [[ -n $queued ]] && (($(date +%s) - $(date -d "$queued" +%s) > 1800)); then
        echo "MACOS $pr $sha"; exit 0
      fi
    fi
  done
  sleep "$interval"
done
echo TIMEOUT
