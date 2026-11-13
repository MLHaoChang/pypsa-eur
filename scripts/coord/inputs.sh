#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Write frontier.py's two input files from the board, never by hand:
#
#   scripts/coord/inputs.sh <board.json> [<outdir>]     # outdir default: the board's folder
#
#   prs.txt              "<pr> <changed path>" per line, for every `prs` row of the
#                        board: each PR's head is fetched (refs/pull/<pr>/head) and
#                        diffed against origin/<base>, so a merged or stale PR never
#                        shows up as an overlap
#   sessions-issues.txt  one "<session> <doing>" line per `sessions` row
#
# Then: frontier.py --open-prs prs.txt --sessions sessions-issues.txt [--stages]
# A PR whose head cannot be fetched is named on stderr and left out.
# Env: PREMERGE_REMOTE (default origin), PREMERGE_BASE (default master).

set -euo pipefail

(($# >= 1 && $# <= 2)) || { echo "usage: $0 <board.json> [<outdir>]" >&2; exit 2; }
board=$1
[[ -f $board ]] || { echo "inputs: no such board $board" >&2; exit 2; }
out=${2:-$(dirname "$board")}
remote=${PREMERGE_REMOTE:-origin}
base=${PREMERGE_BASE:-master}
mkdir -p "$out"

git fetch -q "$remote" "$base" || echo "inputs: could not fetch $remote $base; using the last copy" >&2
python3 - "$board" "$out/sessions-issues.txt" <<'PY' >"$out/.prs-rows"
import json, sys
rows = json.load(open(sys.argv[1]))["rows"]
with open(sys.argv[2], "w") as f:
    for r in rows:
        if r.get("board") == "sessions":
            f.write(f"{r.get('session', '')} {r.get('doing', '')}\n")
for r in rows:
    if r.get("board") == "prs":
        print(r["pr"])
PY

: >"$out/prs.txt"
while read -r pr; do
  [[ $pr =~ ^[0-9]+$ ]] || continue
  if git fetch -q "$remote" "refs/pull/$pr/head" 2>/dev/null; then
    git diff --name-only "$remote/$base...FETCH_HEAD" | sed "s/^/$pr /" >>"$out/prs.txt"
  else
    echo "inputs: cannot fetch refs/pull/$pr/head, left out" >&2
  fi
done <"$out/.prs-rows"
rm -f "$out/.prs-rows"
echo "$out/prs.txt $(wc -l <"$out/prs.txt") lines; $out/sessions-issues.txt $(wc -l <"$out/sessions-issues.txt") lines"
