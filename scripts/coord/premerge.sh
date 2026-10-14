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
# head that is not success or skipped (or that it could not read). Docs-only
# overlap and files identical on both sides are reported, not fatal.
#
# Environment:
#   PREMERGE_REMOTE  remote to fetch from (default: origin)
#   PREMERGE_BASE    base branch (default: master)
#   PREMERGE_REPO    GitHub repo whose check runs are read
#                    (default: MLHaoChang/pypsa-eur)
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

root=$(git rev-parse --show-toplevel)
cd "$root"

# Overlap in these paths is harmless when the merge itself is clean.
docs_only_re='^(docs/superpowers/OPEN-ITEMS\.md|docs/superpowers/plans/.*|doc/release-notes\.d/.*|doc/release_notes\.md)$'
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
fi

echo
echo "== files changed on both sides since merge-base"
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
    else
      echo "  code       $f"
      fail=1
    fi
  done <<<"$overlap"
fi

echo
echo "== semantic tests"
changed=$(git diff --name-only "$mbase" "$head")
if ! grep -Eq "$semantic_re" <<<"$changed"; then
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
  if (cd "$wt/pypsa-gui/backend" && "$py" -m pytest -q -p no:cacheprovider --disable-warnings "${semantic_tests[@]}"); then
    echo "semantic tests: pass"
  else
    echo "semantic tests: FAIL"
    fail=1
  fi

  if grep -Eq "$catalogue_re" <<<"$changed"; then
    echo
    echo "== assistant inventory (harness/catalogue changed)"
    if "$py" "$wt/pypsa-gui/backend/smoke/assistant_inventory.py" --check; then
      echo "inventory: pass"
    else
      echo "inventory: FAIL"
      fail=1
    fi
  fi
fi

echo
echo "== checks"
# Every check run on the head must be success or skipped. Anything else,
# including a run still in progress, blocks; so does failing to read them.
if runs=$(command -v gh >/dev/null &&
  gh api "repos/$gh_repo/commits/$head/check-runs" --paginate \
    --jq '.check_runs[] | [.status, (.conclusion // "-"), .name] | @tsv'); then
  total=$(grep -c . <<<"$runs" || true)
  bad=$(awk -F'\t' 'NF && !($1 == "completed" && ($2 == "success" || $2 == "skipped"))' <<<"$runs")
  echo "$total check run(s) on $head"
  if ((total == 0)); then
    echo "  none yet"
    fail=1
  elif [[ -n $bad ]]; then
    awk -F'\t' '{ printf "  %-11s %-15s %s\n", $1, $2, $3 }' <<<"$bad"
    fail=1
  else
    echo "all success or skipped"
  fi
else
  echo "checks: not read"
  fail=1
fi

echo
if ((fail)); then
  echo "RESULT: blocked"
else
  echo "RESULT: ok to merge $head"
fi
exit "$fail"
