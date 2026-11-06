#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Pre-push check: the fast contract checks the ~2 h CI would otherwise find.
# Run from the repo root of a PR branch before every push:
#
#   scripts/coord/prepush.sh [--allow-skip] [--plan] [<base>]    # base default origin/master
#
# On the files changed since the merge base with <base> it runs: ruff check and
# ruff format --check on changed Python (a file already failing on the base is
# left alone, like the CI ratchet); `tsc --noEmit` when a frontend file changed;
# the tests scripts/coord/overlap_tests.py maps to the changed files (and the
# changed test files themselves); a fixed cross-cutting contract set; the repr
# diff guard. One PASS/FAIL/SKIP line per step; exit non-zero on any FAIL. A step
# whose tool is missing (ruff, node_modules, the backend venv) prints FAIL, never
# PASS; with --allow-skip it prints SKIP and does not fail the run.
# --plan prints the selected steps and files and runs nothing.
#
# Env: PREPUSH_PYTHON (backend venv python, default /root/.venv-pypsa-gui/bin/python), PREPUSH_RUFF.
set -uo pipefail

allow_skip=0 plan=0 base=origin/master
for a in "$@"; do
  case $a in
    --allow-skip) allow_skip=1 ;;
    --plan) plan=1 ;;
    -*) echo "unknown option $a" >&2; exit 2 ;;
    *) base=$a ;;
  esac
done

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root"
mb=$(git merge-base "$base" HEAD) || { echo "no merge base with $base" >&2; exit 2; }
changed=$({ git diff --name-only --diff-filter=d "$mb"; git ls-files --others --exclude-standard; } | sort -u)

py_bin=${PREPUSH_PYTHON:-/root/.venv-pypsa-gui/bin/python}
backend=pypsa-gui/backend
frontend=pypsa-gui/frontend
log=$(mktemp -d)/prepush
mkdir -p "$log"
fails=0 skips=0

result() { # name status detail
  printf '%-4s %s%s\n' "$2" "$1" "${3:+ ($3)}"
  [ "$2" = FAIL ] && fails=$((fails + 1))
  [ "$2" = SKIP ] && skips=$((skips + 1))
  return 0
}
missing() { # name what: FAIL (or SKIP) because a tool is absent
  if [ "$allow_skip" = 1 ]; then result "$1" SKIP "missing: $2"; else result "$1" FAIL "missing: $2"; fi
}
run() { # name cmd... (run in $PWD; log tail on failure)
  local name=$1; shift
  if [ "$plan" = 1 ]; then echo "plan $name: $*"; return 0; fi
  local f="$log/${name//[^A-Za-z0-9]/_}.log" t0=$SECONDS
  if "$@" >"$f" 2>&1; then result "$name" PASS "$((SECONDS - t0))s"
  else result "$name" FAIL "$((SECONDS - t0))s, log $f"; tail -n 12 "$f" | sed 's/^/     | /'; fi
}
backend_env() { PYTHONPATH="$root:$root/$backend" "$@"; }

# --- ruff -----------------------------------------------------------------
pyfiles=$(grep -E '\.py$' <<<"$changed" || true)
if [ -n "$pyfiles" ]; then
  if [ "$plan" = 1 ]; then echo "plan ruff: $(tr '\n' ' ' <<<"$pyfiles")"
  elif ruff_bin=${PREPUSH_RUFF:-$(command -v ruff || echo /root/.venv-pypsa-gui/bin/ruff)}; [ ! -x "$ruff_bin" ]; then
    missing ruff "ruff ($ruff_bin)"
  else
    bad=""
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      for mode in check format; do
        if [ $mode = check ]; then cmd=("$ruff_bin" check --quiet --no-fix); else cmd=("$ruff_bin" format --check --quiet); fi
        if git cat-file -e "$mb:$f" 2>/dev/null; then # on the base: enforce only if it was clean there
          git show "$mb:$f" | "${cmd[@]}" --stdin-filename "$f" - >/dev/null 2>&1 || continue
        fi
        "${cmd[@]}" "$f" >/dev/null 2>&1 || bad+="$f ($mode) "
      done
    done <<<"$pyfiles"
    if [ -n "$bad" ]; then result ruff FAIL "$bad"; else result ruff PASS "$(wc -l <<<"$pyfiles") files"; fi
  fi
fi

# --- tsc ------------------------------------------------------------------
if grep -q "^$frontend/" <<<"$changed"; then
  if [ "$plan" = 1 ]; then echo "plan tsc: $frontend"
  elif [ ! -x "$frontend/node_modules/.bin/tsc" ]; then missing tsc "$frontend/node_modules"
  else (cd "$frontend" && run tsc node_modules/.bin/tsc --noEmit -p .); fi
fi

# --- tests overlap_tests.py maps to the changed files ----------------------
mapped=$(python3 "$here/overlap_tests.py" "$root" $(grep -vE '^$' <<<"$changed" | tr '\n' ' ') 2>/dev/null || true)
# changed test files run themselves (overlap_tests maps them to nothing)
own_py=$(grep -E "^$backend/tests/test_.*\.py$" <<<"$changed" | sed "s#^$backend/##" || true)
own_js=$(grep -E "^$frontend/src/.*\.test\.tsx?$" <<<"$changed" | sed "s#^$frontend/##" || true)
bt=$( { awk -F'\t' '$1=="pytest" && NF==3 {print $3}' <<<"$mapped"; echo "$own_py"; } | grep -v '^$' | sort -u || true)
ft=$( { awk -F'\t' '$1=="vitest" && NF==3 {print $3}' <<<"$mapped"; echo "$own_js"; } | grep -v '^$' | sort -u || true)

need_py() { # name -> 0 when the backend interpreter exists
  [ -x "$py_bin" ] && return 0; missing "$1" "backend python $py_bin (set PREPUSH_PYTHON)"; return 1
}
need_node() { [ -x "$frontend/node_modules/.bin/vitest" ] && return 0; missing "$1" "$frontend/node_modules (vitest)"; return 1; }

if [ -n "$bt" ]; then
  if [ "$plan" = 1 ]; then echo "plan mapped backend tests: $(tr '\n' ' ' <<<"$bt")"
  elif need_py "mapped backend tests"; then
    (cd "$backend" && run "mapped backend tests" backend_env "$py_bin" -m pytest -q $bt); fi
fi
if [ -n "$ft" ]; then
  if [ "$plan" = 1 ]; then echo "plan mapped frontend tests: $(tr '\n' ' ' <<<"$ft")"
  elif need_node "mapped frontend tests"; then
    (cd "$frontend" && run "mapped frontend tests" node_modules/.bin/vitest run $ft); fi
fi

# --- fixed contract set (always) -------------------------------------------
contract_py="tests/test_route_chat_disposition.py tests/test_assurance_returned_shapes.py tests/test_assurance_contracts.py tests/test_tool_error_kind_manifest.py"
contract_js=$(cd "$frontend" 2>/dev/null && { echo src/site3d/site3dUiContract.test.ts; find src -name '*Contract*.test.ts' ; } | sort -u | tr '\n' ' ')
if [ "$plan" = 1 ]; then
  echo "plan contract pytest: $contract_py"
  echo "plan contract smoke: assistant_route_parity --check, assistant_control_matrix --check"
  echo "plan contract vitest: $contract_js"
elif need_py "contract tests"; then
  (cd "$backend" && run "contract: pytest" backend_env "$py_bin" -m pytest -q $contract_py)
  (cd "$backend" && run "contract: route parity" backend_env "$py_bin" -m smoke.assistant_route_parity --check)
  (cd "$backend" && run "contract: control matrix" backend_env "$py_bin" -m smoke.assistant_control_matrix --check)
fi
if [ "$plan" != 1 ] && need_node "contract: vitest"; then
  (cd "$frontend" && run "contract: vitest" node_modules/.bin/vitest run $contract_js)
fi

# --- repr diff guard ----------------------------------------------------------
if [ "$plan" = 1 ]; then echo "plan repr guard: $here/repr_guard.py $base"
else run "repr guard" python3 "$here/repr_guard.py" "$base"; fi

[ "$plan" = 1 ] && exit 0
echo
if [ "$fails" -gt 0 ]; then echo "PREPUSH: FAIL ($fails failing, $skips skipped)"; exit 1; fi
echo "PREPUSH: PASS ($skips skipped)"
