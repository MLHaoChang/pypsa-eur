#!/usr/bin/env bash
# Pre-merge check for one pull request against the base branch. See
# .claude/skills/steward/SKILL.md for when to run it.
#
#   scripts/coord/premerge.sh <pr-number>
#
# This is the whole merge gate: one command, nothing checked by hand. It exits
# non-zero on a conflict, a code-file overlap with the base branch, a failing
# semantic check on the trial merge, or any check run on the head that is not
# success or skipped (or that it could not read). Docs-only overlap and files
# identical on both sides are reported, not fatal. Reading check runs needs
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
# merge is clean and semantic_cmd passes on it (e.g. a generated inventory that
# many PRs touch). Leave empty to treat every non-docs overlap as code.
waive_overlap_re=''
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
waive_overlap=()  # overlap files that pass if the trial merge and semantic_cmd do
overlap=$(comm -12 \
  <(git diff --name-only "$mbase" "$base_sha" | sort) \
  <(git diff --name-only "$mbase" "$head" | sort))
if [[ -z $overlap ]]; then
  echo "none"
else
  code_overlap=0
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
    else
      echo "  code       $f"
      fail=1
      code_overlap=$((code_overlap + 1))
    fi
  done <<<"$overlap"
  if ((code_overlap)); then
    reasons+=("overlap: $code_overlap code file$([[ $code_overlap -eq 1 ]] || echo s)")
  fi
fi

echo
echo "== semantic tests"
changed=$(git diff --name-only "$mbase" "$head")
sem_ok=0
if { [[ -z $semantic_re ]] || ! grep -Eq "$semantic_re" <<<"$changed"; } && ((${#waive_overlap[@]} == 0)); then
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
  if (cd "$wt" && "${semantic_cmd[@]}"); then
    echo "semantic tests: pass"
    sem_ok=1
  else
    echo "semantic tests: FAIL"
    fail=1
    reasons+=("semantic tests failed")
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
