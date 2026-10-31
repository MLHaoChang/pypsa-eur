#!/usr/bin/env bash
# Pre-merge check for one pull request against the base branch. It also reads
# the PR's GK/UX verdict comments (see "reviewer verdicts"). See
# .claude/skills/steward/SKILL.md for when to run it.
#
#   scripts/coord/premerge.sh <pr-number>
#
# This is the whole merge gate: one command, nothing checked by hand. It exits
# non-zero on a conflict, a code-file overlap with the base branch, a failing
# semantic check on the trial merge, or any check run on the head that is not
# success or skipped (or that it could not read). Docs-only overlap and files
# identical on both sides are reported, not fatal. With overlap_tests_script set,
# a code overlap is waived when the tests that exercise each file pass too. Reading check runs needs
# the gh CLI.
#
# Environment:
#   PREMERGE_REMOTE  remote to fetch from (default: origin)
#   PREMERGE_BASE    base branch (default: {{BASE_BRANCH}})
#   PREMERGE_REPO    GitHub repo whose check runs are read (default: {{REPO}})

set -euo pipefail

usage() { echo "usage: $0 <pr-number>" >&2; exit 2; }
[[ $# -eq 1 && $1 =~ ^[0-9]+$ ]] || usage
pr=$1
remote=${PREMERGE_REMOTE:-origin}
base_branch=${PREMERGE_BASE:-{{BASE_BRANCH}}}
gh_repo=${PREMERGE_REPO:-{{REPO}}}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(git rev-parse --show-toplevel)
cd "$root"

# --- Project settings ----------------------------------------------------
# Overlap in these paths is harmless when the merge itself is clean.
docs_only_re='^({{RELEASE_NOTES_DIR}}/.*|docs/.*\.md|(.*/)?(CONTEXT|GLOSSARY)\.md|GLOSSARY-MAP\.md)$'
# A PR touching a path matching semantic_re must pass semantic_cmd on a trial
# merge with the base branch. Leave semantic_re empty to skip this check.
# semantic_cmd runs from the trial-merge worktree root.
semantic_re=''
semantic_cmd=(python3 -m pytest -q -p no:cacheprovider)
# Overlap only in paths matching waive_overlap_re does not block when the trial
# merge is clean and semantic_cmd passes on it. Leave empty to treat every
# non-docs overlap as code. Better than both waivers below: commit no generated
# file (build it in the check) and give each shared append-only file a `.d/`
# fragment directory, one new file per PR (see LEARNINGS.md, 27 Oct 2026).
waive_overlap_re=''
# Overlap only in a manifest matching append_only_overlap_re does not block when
# both sides only add lines to it, the trial merge is clean and
# append_only_test_cmd (run from the trial-merge worktree root) passes on it.
# Leave empty to treat such a manifest as code.
append_only_overlap_re=''
append_only_test_cmd=()
# A code overlap does not block when the trial merge is clean, semantic_cmd
# passes and the tests that exercise each overlapping file pass. Set
# overlap_tests_script to a script (path relative to this one) that is called as
# `<script> <trial-worktree> <file>...` and prints, per file, `test<TAB>file<TAB>
# test-id` for each test or `none<TAB>file`. overlap_test_cmd (run from the
# worktree root, test ids appended) runs them. A file with no test still blocks.
# Leave empty to treat every non-docs overlap as code.
overlap_tests_script=''
overlap_test_cmd=(python3 -m pytest -q -p no:cacheprovider)
# -------------------------------------------------------------------------

base_ref="refs/remotes/$remote/$base_branch"
pr_ref="refs/premerge/pr-$pr"
git fetch -q "$remote" \
  "+refs/heads/$base_branch:$base_ref" \
  "+refs/pull/$pr/head:$pr_ref"

head=$(git rev-parse "$pr_ref")
base_sha=$(git rev-parse "$base_ref")
mbase=$(git merge-base "$base_sha" "$head")
behind=$(git rev-list --count --first-parent "$mbase..$base_sha")

echo "PR #$pr"
echo "head:       $head"
echo "$base_branch:     $base_sha"
echo "merge-base: $mbase"
echo "behind:     $behind merge(s) on $base_branch since merge-base"

fail=0
reasons=()  # why the PR is blocked; appended to the RESULT line

echo
echo "== conflicts"
if tree=$(git merge-tree --write-tree --name-only "$base_sha" "$head"); then
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
waive_overlap=()  # overlap files that pass if the trial merge and semantic_cmd do
append_overlap=()  # append-only manifest overlap, waived if only lines were added (below)
overlap=$(comm -12 \
  <(git diff --name-only "$mbase" "$base_sha" | sort) \
  <(git diff --name-only "$mbase" "$head" | sort))
if [[ -z $overlap ]]; then
  echo "none"
else
  while IFS= read -r f; do
    # Same blob on both sides (e.g. a cherry-pick the base already has), or
    # deleted on both: nothing to reconcile.
    if [[ $(git rev-parse -q --verify "$base_sha:$f" || true) == \
      "$(git rev-parse -q --verify "$head:$f" || true)" ]]; then
      echo "  identical  $f"
    elif [[ $f =~ $docs_only_re ]]; then
      echo "  docs-only  $f"
    elif [[ -n $waive_overlap_re && $f =~ $waive_overlap_re ]]; then
      echo "  waivable   $f (decided on the trial merge, below)"
      waive_overlap+=("$f")
    elif [[ -n $append_only_overlap_re && $f =~ $append_only_overlap_re ]]; then
      echo "  append     $f (decided on the trial merge, below)"
      append_overlap+=("$f")
    else
      echo "  code       $f"
      code_files+=("$f")
    fi
  done <<<"$overlap"
fi

echo
echo "== semantic tests"
changed=$(git diff --name-only "$mbase" "$head")
sem_ok=0
unmapped=() overlap_tests=()
need_sem=0
{ { [[ -n $semantic_re ]] && grep -Eq "$semantic_re" <<<"$changed"; } \
  || ((${#waive_overlap[@]} || ${#append_overlap[@]})); } && need_sem=1
if ((!need_sem)) && { [[ -z $overlap_tests_script ]] || ((${#code_files[@]} == 0)); }; then
  echo "not needed (no path matches semantic_re)"
elif ((conflict)); then
  echo "skipped: no trial merge while the PR conflicts"
else
  trial=$(GIT_AUTHOR_NAME=premerge GIT_AUTHOR_EMAIL=premerge@localhost \
    GIT_COMMITTER_NAME=premerge GIT_COMMITTER_EMAIL=premerge@localhost \
    git commit-tree "$tree" -p "$base_sha" -p "$head" -m "premerge trial #$pr")
  wt=$(mktemp -d "${TMPDIR:-/tmp}/premerge-$pr.XXXXXX")
  trap 'git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"' EXIT
  git worktree add -q --detach "$wt" "$trial"

  echo "trial merge $trial"

  # The tests that exercise each code-overlap file; a file with none still blocks.
  if [[ -n $overlap_tests_script ]] && ((${#code_files[@]})); then
    while IFS=$'\t' read -r kind f t; do
      if [[ $kind == none ]]; then unmapped+=("$f"); else overlap_tests+=("$t"); fi
    done < <("$script_dir/$overlap_tests_script" "$wt" "${code_files[@]}")
    ((${#unmapped[@]} == 0)) && need_sem=1
  fi

  if ((!need_sem)); then
    echo "not needed (no path matches semantic_re)"
  elif (cd "$wt" && "${semantic_cmd[@]}"); then
    echo "semantic tests: pass"
    sem_ok=1
  else
    echo "semantic tests: FAIL"
    fail=1
    reasons+=("semantic tests failed")
  fi
  if ((${#append_overlap[@]})) && ((${#append_only_test_cmd[@]})); then
    if (cd "$wt" && "${append_only_test_cmd[@]}"); then
      echo "append-only manifest test: pass"
    else
      echo "append-only manifest test: FAIL"
      sem_ok=0
      fail=1
      reasons+=("append-only manifest test failed")
    fi
  fi
fi

if ((${#code_files[@]})); then
  if [[ -z $overlap_tests_script ]]; then
    ok=0
  else
    echo
    echo "== code overlap tests"
    ok=0
    if ((conflict)); then
      echo "  no trial merge while the PR conflicts"
    elif ((${#unmapped[@]})); then
      printf '  no test     %s\n' "${unmapped[@]}"
      echo "  these files have no test, so the overlap blocks"
    elif ((!sem_ok)); then
      echo "  semantic tests did not pass on the trial merge, so the overlap blocks"
    else
      ok=1
      mapfile -t uniq_tests < <(printf '%s\n' "${overlap_tests[@]}" | sort -u)
      (cd "$wt" && "${overlap_test_cmd[@]}" "${uniq_tests[@]}") || ok=0
      ((ok)) || echo "  the overlap tests failed on the trial merge, so the overlap blocks"
    fi
    ((ok)) && echo "  waived      code overlap (${#code_files[@]} file(s)): clean trial merge, semantic pass, ran: ${uniq_tests[*]}"
  fi
  if ((!ok)); then
    fail=1
    reasons+=("overlap: ${#code_files[@]} code file$([[ ${#code_files[@]} -eq 1 ]] || echo s)")
  fi
fi

if ((${#waive_overlap[@]})); then
  echo
  echo "== waivable overlap"
  if ((!conflict && sem_ok)); then
    echo "  waived      overlap in waive_overlap_re: clean trial merge, semantic pass"
  else
    printf '  code        %s\n' "${waive_overlap[@]}"
    fail=1
    reasons+=("overlap: ${#waive_overlap[@]} code file$([[ ${#waive_overlap[@]} -eq 1 ]] || echo s)")
  fi
fi

if ((${#append_overlap[@]})); then
  echo
  echo "== append-only overlap"
  removed=0
  for f in "${append_overlap[@]}"; do
    removed=$(( removed + $(git diff -U0 "$mbase" "$base_sha" -- "$f" | grep -c '^-[^-]' || true) \
      + $(git diff -U0 "$mbase" "$head" -- "$f" | grep -c '^-[^-]' || true) ))
  done
  if ((!conflict && sem_ok && removed == 0)); then
    echo "  waived      append-only overlap: append-only, clean trial merge, manifest test pass"
  else
    printf '  code        %s\n' "${append_overlap[@]}"
    fail=1
    reasons+=("overlap: ${#append_overlap[@]} manifest file$([[ ${#append_overlap[@]} -eq 1 ]] || echo s) not append-only or not verified")
  fi
fi

echo
echo "== builder session"
# Reviewers send NOT OK details straight to the builder named on the PR body's
# "Session: <id>" line. Missing it is reported, not blocking.
if sid=$(command -v gh >/dev/null && gh api "repos/$gh_repo/pulls/$pr" --jq .body \
  | grep -oE 'Session: session_[A-Za-z0-9]+' | tail -1) && [[ -n $sid ]]; then
  echo "$sid"
else
  echo "no 'Session: <id>' line in the PR body: ask the PR's session to add it"
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
    if [[ -z $p2 || -n $extra ]] || ! git merge-base --is-ancestor "$p2" "$base_sha" \
      || ! mt=$(git merge-tree --write-tree "$p1" "$p2") \
      || [[ ${mt%%$'\n'*} != "$(git rev-parse "$c^{tree}")" ]]; then
      ok=0; break
    fi
  done < <(git rev-list --first-parent --reverse --parents "$x..$head")
  ((ok)) && return 0
  mt=$(git merge-tree --write-tree "$x" "$base_sha" 2>/dev/null) && [[ ${mt%%$'\n'*} == "$(git rev-parse "$head^{tree}")" ]]
}

if verdicts=$(command -v gh >/dev/null &&
  gh api "repos/$gh_repo/issues/$pr/comments" --paginate \
    --jq '.[] | {login: .user.login, body: .body}' | python3 "$(dirname "$0")/verdicts.py"); then
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
      files=$(git diff --name-only "$full" "$head" 2>/dev/null | sed -n 1,15p | paste -sd' ')
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
