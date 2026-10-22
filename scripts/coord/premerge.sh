#!/usr/bin/env bash
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
#
# Pre-merge check for one pull request against master. See
# .claude/skills/steward/SKILL.md for when to run it.
#
#   scripts/coord/premerge.sh <pr-number>
#
# This is the whole merge gate: one command, nothing checked by hand. It exits
# non-zero on a conflict, a code-file overlap with master, a failing semantic
# test or catalogue inventory check on the trial merge, or any check run on the
# head that is not success or skipped (or that it could not read), a GK/UX
# "NOT OK" PR comment newer than that reviewer's last OK, or a live
# coordination-file change with no matching template change. Docs-only
# overlap and files identical on both sides are reported, not fatal. A code
# overlap is waived when the trial merge is clean, the semantic tests pass and
# the tests that exercise each overlapping file pass (see "code overlap tests").
#
# Environment:
#   PREMERGE_REMOTE  remote to fetch from (default: origin)
#   PREMERGE_BASE    base branch (default: master)
#   PREMERGE_REPO    GitHub repo whose check runs are read
#                    (default: MLHaoChang/pypsa-eur)
#   PREMERGE_TEMPLATE_SYNC  "skip" to pass a live coordination-file change
#                    that is project-specific and needs no template change
#   PREMERGE_VITEST  command that runs frontend tests, given test files
#                    (default: npx vitest run)
#   PREMERGE_PYTHON  Python that runs the semantic tests (default: the repo's
#                    pixi `test` env, then `default`, then ~/.venv-pypsa-gui
#                    from .claude/hooks/session-start.sh, then python3)

set -euo pipefail

usage() { echo "usage: $0 <pr-number>" >&2; exit 2; }
[[ $# -eq 1 && $1 =~ ^[0-9]+$ ]] || usage
pr=$1
remote=${PREMERGE_REMOTE:-origin}
base_branch=${PREMERGE_BASE:-master}
gh_repo=${PREMERGE_REPO:-MLHaoChang/pypsa-eur}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(git rev-parse --show-toplevel)
cd "$root"

# Overlap in these paths is harmless when the merge itself is clean.
docs_only_re='^(docs/superpowers/OPEN-ITEMS\.md|docs/superpowers/plans/.*|doc/release-notes\.d/.*|doc/release_notes\.md|(.*/)?(CONTEXT|GLOSSARY)\.md|GLOSSARY-MAP\.md|pypsa-gui/backend/harness/README\.md)$'
# A PR touching these must pass the semantic tests on the trial merge: backend
# routes (project routes and routers/mcp among them), chat tools, the project
# ACL services and the harness MCP server.
semantic_re='^pypsa-gui/backend/routers/|(^|/)harness/catalogue|(^|/)harness/toolsets|(^|/)harness/mcp_server|(^|/)services/chat_tools|(^|/)chat_tools|(^|/)services/project_(acl|registry)\.py$'
# The one list of semantic tests; .claude/skills/steward/SKILL.md points here.
semantic_tests=(
  tests/test_write_surface_lock_policy.py
  tests/test_chat_tools_handler_dependencies.py
  tests/test_chat_tools_argshape.py
  tests/test_project_acl.py
  tests/test_project_route_acl_policy.py
  tests/test_harness_mcp_server.py
)
# A PR touching these must also keep the assistant inventory current.
catalogue_re='(^|/)harness/catalogue'
# Overlap only in these files does not block when the trial merge is clean and
# the semantic tests and the assistant inventory check pass on it.
inventory_overlap_re='^(pypsa-gui/backend/harness/catalogue\.py|\.scratch/live-voice/capability-inventory\.json)$'
# Overlap only in this manifest does not block when both sides only add lines
# to it, the trial merge is clean and the manifest test passes on it.
kinds_overlap_re='^pypsa-gui/tool-error-kinds\.json$'
kinds_test=tests/test_tool_error_kind_manifest.py
# A PR touching these live coordination files must also update
# templates/agent-coordination/ (or run with PREMERGE_TEMPLATE_SYNC=skip).
template_live_re='^(\.claude/skills/(steward|coordinator|retro)/|scripts/coord/|docs/agents/session-brief\.md$)'

base_ref="refs/remotes/$remote/$base_branch"
pr_ref="refs/premerge/pr-$pr"
git fetch -q "$remote" \
  "+refs/heads/$base_branch:$base_ref" \
  "+refs/pull/$pr/head:$pr_ref"

head=$(git rev-parse "$pr_ref")
master=$(git rev-parse "$base_ref")
mbase=$(git merge-base "$master" "$head")
behind=$(git rev-list --count --first-parent "$mbase..$master")

echo "PR #$pr"
echo "head:       $head"
echo "$base_branch:     $master"
echo "merge-base: $mbase"
echo "behind:     $behind merge(s) on $base_branch since merge-base"

fail=0
reasons=()  # why the PR is blocked; appended to the RESULT line

echo
echo "== conflicts"
if tree=$(git merge-tree --write-tree --name-only "$master" "$head"); then
  tree=${tree%%$'\n'*}
  echo "clean"
  conflict=0
else
  echo "CONFLICT:"
  # First line is the tree OID; the conflicted paths follow up to a blank line.
  sed -n '2,/^$/p' <<<"$tree" | sed '/^$/d; s/^/  /'
  conflict=1
  fail=1
  reasons+=("conflict")
fi

echo
echo "== files changed on both sides since merge-base"
code_files=()  # code overlap, waived if its tests pass on the trial merge (below)
inv_overlap=()  # overlap files that pass if the trial merge, tests and inventory do
kinds_overlap=()  # tool-error-kinds.json overlap, waived if append-only (below)
overlap=$(comm -12 \
  <(git diff --name-only "$mbase" "$master" | sort) \
  <(git diff --name-only "$mbase" "$head" | sort))
if [[ -z $overlap ]]; then
  echo "none"
else
  while IFS= read -r f; do
    # Same blob on both sides (e.g. a cherry-pick master already has), or
    # deleted on both: nothing to reconcile.
    if [[ $(git rev-parse -q --verify "$master:$f" || true) == \
      "$(git rev-parse -q --verify "$head:$f" || true)" ]]; then
      echo "  identical  $f"
    elif [[ $f =~ $docs_only_re ]]; then
      echo "  docs-only  $f"
    elif [[ $f =~ $inventory_overlap_re ]]; then
      echo "  inventory  $f (decided on the trial merge, below)"
      inv_overlap+=("$f")
    elif [[ $f =~ $kinds_overlap_re ]]; then
      echo "  kinds      $f (decided on the trial merge, below)"
      kinds_overlap+=("$f")
    else
      echo "  code       $f"
      code_files+=("$f")
    fi
  done <<<"$overlap"
fi

echo
echo "== semantic tests"
changed=$(git diff --name-only "$mbase" "$head")
sem_ok=0 inv_ok=0
tests_to_run=("${semantic_tests[@]}")
((${#kinds_overlap[@]})) && tests_to_run+=("$kinds_test")
need_sem=0
{ grep -Eq "$semantic_re" <<<"$changed" || ((${#inv_overlap[@]} || ${#kinds_overlap[@]})); } && need_sem=1
unmapped=() pytest_files=() vitest_files=() tested=()
if ((!need_sem && ${#code_files[@]} == 0)); then
  echo "not needed (no route, chat-tool, project-ACL or MCP-server change)"
elif ((conflict)); then
  echo "skipped: no trial merge while the PR conflicts"
else
  py=${PREMERGE_PYTHON:-}
  if [[ -z $py ]]; then
    for cand in "$root/.pixi/envs/test/bin/python" "$root/.pixi/envs/default/bin/python" \
      "$HOME/.venv-pypsa-gui/bin/python"; do
      [[ -x $cand ]] && { py=$cand; break; }
    done
  fi
  py=${py:-python3}

  trial=$(GIT_AUTHOR_NAME=premerge GIT_AUTHOR_EMAIL=premerge@localhost \
    GIT_COMMITTER_NAME=premerge GIT_COMMITTER_EMAIL=premerge@localhost \
    git commit-tree "$tree" -p "$master" -p "$head" -m "premerge trial #$pr")
  wt=$(mktemp -d "${TMPDIR:-/tmp}/premerge-$pr.XXXXXX")
  trap 'git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"' EXIT
  git worktree add -q --detach "$wt" "$trial"

  # Cloud containers export a PYTHONPATH naming the main checkout; replace it
  # so every import resolves inside the trial merge.
  export PYTHONPATH="$wt/pypsa-gui/backend:$wt"
  echo "trial merge $trial, python $py"

  # The tests that exercise each code-overlap file; a file with none still blocks.
  if ((${#code_files[@]})); then
    while IFS=$'\t' read -r runner f t; do
      case $runner in
        none) unmapped+=("$f") ;;
        pytest) pytest_files+=("$t"); tested+=("$t") ;;
        vitest) vitest_files+=("$t"); tested+=("$t") ;;
      esac
    done < <(python3 "$script_dir/overlap_tests.py" "$wt" "${code_files[@]}")
  fi
  ((${#code_files[@]} && ${#unmapped[@]} == 0)) && need_sem=1

  if ((!need_sem)); then
    echo "not needed (no route, chat-tool, project-ACL or MCP-server change)"
  elif (cd "$wt/pypsa-gui/backend" && "$py" -m pytest -q -p no:cacheprovider --disable-warnings "${tests_to_run[@]}"); then
    echo "semantic tests: pass"
    sem_ok=1
  else
    echo "semantic tests: FAIL"
    fail=1
    reasons+=("semantic tests failed")
  fi

  if grep -Eq "$catalogue_re" <<<"$changed" || ((${#inv_overlap[@]})); then
    echo
    echo "== assistant inventory (harness/catalogue changed)"
    if "$py" "$wt/pypsa-gui/backend/smoke/assistant_inventory.py" --check; then
      echo "inventory: pass"
      inv_ok=1
    else
      echo "inventory: FAIL"
      fail=1
      reasons+=("inventory check failed")
    fi
  fi
fi

if ((${#code_files[@]})); then
  echo
  echo "== code overlap tests"
  ok=0
  if ((conflict)); then
    echo "  no trial merge while the PR conflicts"
  elif ((${#unmapped[@]})); then
    printf '  no test     %s\n' "${unmapped[@]}"
    echo "  these files have no test file (backend: tests/test_<mod>*.py; frontend: <Name>*.test.*), so the overlap blocks"
  elif ((!sem_ok)); then
    echo "  the semantic tests did not pass on the trial merge, so the overlap blocks"
  else
    ok=1
    unique_py=$(printf '%s\n' "${pytest_files[@]}" | sort -u)
    unique_vt=$(printf '%s\n' "${vitest_files[@]}" | sort -u)
    if [[ -n $unique_py ]]; then
      mapfile -t pyfiles <<<"$unique_py"
      (cd "$wt/pypsa-gui/backend" && "$py" -m pytest -q -p no:cacheprovider --disable-warnings "${pyfiles[@]}") || ok=0
    fi
    if [[ -n $unique_vt ]]; then
      mapfile -t vtfiles <<<"$unique_vt"
      [[ -e $wt/pypsa-gui/frontend/node_modules ]] \
        || ln -s "$root/pypsa-gui/frontend/node_modules" "$wt/pypsa-gui/frontend/node_modules" 2>/dev/null || true
      read -r -a vt_cmd <<<"${PREMERGE_VITEST:-npx vitest run}"
      (cd "$wt/pypsa-gui/frontend" && "${vt_cmd[@]}" "${vtfiles[@]}") || ok=0
    fi
  fi
  if ((ok)); then
    echo "  waived      code overlap (${#code_files[@]} file(s)): clean trial merge, semantic tests pass, ran: $(printf '%s\n' "${tested[@]}" | sort -u | paste -sd' ')"
  else
    ((conflict || ${#unmapped[@]} || !sem_ok)) || echo "  the overlap tests failed on the trial merge, so the overlap blocks"
    fail=1
    reasons+=("overlap: ${#code_files[@]} code file$([[ ${#code_files[@]} -eq 1 ]] || echo s)")
  fi
fi

if ((${#inv_overlap[@]})); then
  echo
  echo "== catalogue/inventory overlap"
  if ((!conflict && sem_ok && inv_ok)); then
    echo "  waived      catalogue/inventory overlap: clean trial merge, inventory + semantic pass"
  else
    printf '  code        %s\n' "${inv_overlap[@]}"
    fail=1
    reasons+=("overlap: ${#inv_overlap[@]} code file$([[ ${#inv_overlap[@]} -eq 1 ]] || echo s)")
  fi
fi

if ((${#kinds_overlap[@]})); then
  echo
  echo "== tool-error-kinds overlap"
  kinds_file=${kinds_overlap[0]}
  removed=$(( $(git diff -U0 "$mbase" "$master" -- "$kinds_file" | grep -c '^-[^-]' || true) \
    + $(git diff -U0 "$mbase" "$head" -- "$kinds_file" | grep -c '^-[^-]' || true) ))
  if ((!conflict && sem_ok && removed == 0)); then
    echo "  waived      tool-error-kinds overlap: append-only, clean trial merge, manifest test pass"
  else
    echo "  code        $kinds_file (removed lines: $removed)"
    fail=1
    reasons+=("overlap: tool-error-kinds.json not append-only or not verified")
  fi
fi

echo
echo "== template sync"
# The live coordination files and templates/agent-coordination/ change
# together; see that folder's README.md, "Keeping it current".
changed=${changed:-$(git diff --name-only "$mbase" "$head")}
live=$(grep -E "$template_live_re" <<<"$changed" || true)
if [[ -z $live ]]; then
  echo "not needed (no live coordination file changed)"
elif grep -q '^templates/agent-coordination/' <<<"$changed"; then
  echo "template changed alongside:"
  sed 's/^/  /' <<<"$live"
elif [[ ${PREMERGE_TEMPLATE_SYNC:-} == skip ]]; then
  echo "skipped (PREMERGE_TEMPLATE_SYNC=skip: project-specific change):"
  sed 's/^/  /' <<<"$live"
else
  echo "TEMPLATE NOT UPDATED. Port these to templates/agent-coordination/, or rerun"
  echo "with PREMERGE_TEMPLATE_SYNC=skip if the change is project-specific:"
  sed 's/^/  /' <<<"$live"
  fail=1
  reasons+=("template not updated")
fi

echo
echo "== reviewer verdicts"
# The gatekeeper (GK) and UX reviewer post "GK OK @<sha>: ..." / "GK NOT OK @<sha>: ..."
# (and UX) as PR comments. A missing or stale OK is reported, not blocking:
# premerge cannot know whether this PR needs that review. A NOT OK newer than
# the reviewer's last OK blocks. An OK carries to the head when every commit
# after it is a clean merge of $base_branch, or when its tree merged with the
# current $base_branch equals the head tree.
verdict_carries() { # verdict_carries <full-sha>: succeeds when an OK at <sha> still holds for the head
  local x=$1 c p1 p2 extra mt ok=1
  git merge-base --is-ancestor "$x" "$head" || return 1
  while read -r c p1 p2 extra; do
    # A clean master merge: two parents, the second already in master, and a
    # tree equal to merge-tree of the two parents.
    if [[ -z $p2 || -n $extra ]] || ! git merge-base --is-ancestor "$p2" "$master" \
      || ! mt=$(git merge-tree --write-tree "$p1" "$p2") \
      || [[ ${mt%%$'\n'*} != "$(git rev-parse "$c^{tree}")" ]]; then
      ok=0; break
    fi
  done < <(git rev-list --first-parent --reverse --parents "$x..$head")
  ((ok)) && return 0
  mt=$(git merge-tree --write-tree "$x" "$master" 2>/dev/null) && [[ ${mt%%$'\n'*} == "$(git rev-parse "$head^{tree}")" ]]
}

if verdicts=$(command -v gh >/dev/null &&
  gh api "repos/$gh_repo/issues/$pr/comments" --paginate \
    --jq '.[] | .body | split("\n")[] | capture("^(?<who>GK|UX) (?<v>NOT OK|OK) @(?<sha>[0-9a-f]{7,40})") | [.who, .v, .sha] | @tsv'); then
  for who in GK UX; do
    mine=$(awk -F'\t' -v w="$who" '$1 == w' <<<"$verdicts")
    if [[ -z $mine ]]; then
      echo "$who: no verdict posted (not blocking: premerge cannot tell whether this PR needs it)"
      continue
    fi
    last_ok=$(awk -F'\t' '$2 == "OK" { n = NR } END { print n + 0 }' <<<"$mine")
    last_no=$(awk -F'\t' '$2 == "NOT OK" { n = NR } END { print n + 0 }' <<<"$mine")
    if ((last_no > last_ok)); then
      sha=$(sed -n "${last_no}p" <<<"$mine" | cut -f3)
      echo "$who NOT OK @$sha is newer than the last $who OK: blocks"
      fail=1
      reasons+=("$who NOT OK")
    fi
    ((last_ok)) || continue
    x=$(sed -n "${last_ok}p" <<<"$mine" | cut -f3)
    full=$(git rev-parse -q --verify "$x^{commit}" 2>/dev/null || true)
    if [[ -z $full ]]; then
      echo "$who OK @$x: unknown commit (not fetched), cannot compare to the head"
    elif [[ $full == "$head" ]]; then
      echo "$who OK @$full: current"
    elif verdict_carries "$full"; then
      echo "$who OK @$full: carries (clean $base_branch merge)"
    else
      files=$(git diff --name-only "$full" "$head" 2>/dev/null | head -15 | paste -sd' ')
      echo "$who OK @$full: stale: changed since: ${files:-unreadable}"
    fi
  done
else
  echo "verdicts: not read"
  fail=1
  reasons+=("verdicts: not read")
fi

echo
echo "== checks"
# Every check run on the head must be success or skipped. Anything else,
# including a run still in progress, blocks; so does failing to read them.
if runs=$(command -v gh >/dev/null &&
  gh api "repos/$gh_repo/commits/$head/check-runs" --paginate \
    --jq '.check_runs[] | [.status, (.conclusion // "-"), .name, .id] | @tsv'); then
  total=$(grep -c . <<<"$runs" || true)
  bad=$(awk -F'\t' 'NF && !($1 == "completed" && ($2 == "success" || $2 == "skipped"))' <<<"$runs")
  # An Integration (macos) job cancelled without ever getting a runner (no
  # runner_id, no steps) does not block when Integration (ubuntu) passed on
  # this head. A macOS job that ran, failed or was cancelled mid-run still blocks.
  waived=
  if grep -qP '^completed\tsuccess\tIntegration \(ubuntu\)\t' <<<"$runs"; then
    while IFS=$'\t' read -r st con name id; do
      [[ $st == completed && $con == cancelled && $name == "Integration (macos)" ]] || continue
      [[ $(gh api "repos/$gh_repo/actions/jobs/$id" \
        --jq '((.runner_id // 0) == 0) and ((.steps // []) | length == 0)' 2>/dev/null) == true ]] || continue
      waived+="  waived      cancelled       Integration (macos): no runner assigned; ubuntu twin passed"$'\n'
      bad=$(awk -F'\t' -v id="$id" '$4 != id' <<<"$bad")
    done <<<"$bad"
  fi
  echo "$total check run(s) on $head"
  [[ -n $waived ]] && printf '%s' "$waived"
  if ((total == 0)); then
    echo "  none yet"
    if (($(date +%s) - $(git log -1 --format=%ct "$head") > 600)); then
      echo "  no CI ran on this head (e.g. base retargeted after the push): merge $base_branch in, as a real merge commit, to trigger it"
    fi
    fail=1
    reasons+=("checks: none yet")
  elif [[ -n $bad ]]; then
    awk -F'\t' '{ printf "  %-11s %-15s %s\n", $1, $2, $3 }' <<<"$bad"
    fail=1
    running=$(awk -F'\t' 'NF && $1 != "completed"' <<<"$bad" | grep -c . || true)
    failed=$(awk -F'\t' 'NF && $1 == "completed"' <<<"$bad" | grep -c . || true)
    msg=()
    ((running)) && msg+=("$running running")
    ((failed)) && msg+=("$failed failed")
    reasons+=("checks: $(IFS=,; echo "${msg[*]}" | sed 's/,/, /g')")
  else
    echo "all success or skipped"
  fi
else
  echo "checks: not read"
  fail=1
  reasons+=("checks: not read")
fi

echo
if ((fail)); then
  echo "RESULT: blocked ($(IFS=';'; echo "${reasons[*]}" | sed 's/;/; /g'))"
else
  echo "RESULT: ok to merge $head"
fi
exit "$fail"
