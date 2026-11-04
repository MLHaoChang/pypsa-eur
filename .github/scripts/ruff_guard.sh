#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Ratchet for pypsa-gui/backend Python: a file a PR adds, or one that already
# passed `ruff format --check` / `ruff check` on the base, must still pass.
# Files that were already unformatted on the base are left alone, so the PR
# that touches them is not forced into a whole-file reformat (most of the
# backend predates the formatter). Ruff runs at the version pinned in
# .pre-commit-config.yaml. Usage: ruff_guard.sh <base-ref-or-sha>
set -euo pipefail
base=${1:?usage: ruff_guard.sh <base>}
scope=pypsa-gui/backend
ver=$(sed -n '/ruff-pre-commit/,/hooks:/s/^ *rev: v\(.*\)$/\1/p' .pre-commit-config.yaml)
[ -n "$ver" ] || { echo "cannot read ruff version from .pre-commit-config.yaml"; exit 2; }
have=$(ruff --version 2>/dev/null | awk '{print $2}' || true)
[ "$have" = "$ver" ] || { echo "need ruff $ver, found '${have:-none}'"; exit 2; }

fail=0
while IFS=$'\t' read -r status path; do
  [ -f "$path" ] || continue
  for mode in format check; do
    if [ "$mode" = format ]; then cmd=(ruff format --check --quiet); else cmd=(ruff check --quiet --no-fix); fi
    if [ "$status" != A ]; then
      # Modified file: enforce only if the base version was clean.
      git show "$base:$path" | "${cmd[@]}" --stdin-filename "$path" - >/dev/null 2>&1 || continue
    fi
    if ! "${cmd[@]}" "$path" >/dev/null 2>&1; then
      echo "::error file=$path::ruff $mode fails (run: ruff $mode $path, ruff v$ver)"
      fail=1
    fi
  done
done < <(git diff --name-status --diff-filter=AM "$base"...HEAD -- "$scope/**.py" "$scope/*.py" | awk -F'\t' '{print substr($1,1,1) "\t" $NF}')
exit $fail
