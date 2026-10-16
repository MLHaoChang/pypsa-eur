#!/bin/bash
# Tests for block-dangerous-git.sh. Run: .claude/hooks/test-block-dangerous-git.sh
here=$(cd "$(dirname "$0")" && pwd)
hook="$here/block-dangerous-git.sh"
fail=0

expect() { # expect <allow|block> <command>
  local want=$1 cmd=$2 got
  jq -n --arg c "$cmd" '{tool_input: {command: $c}}' | "$hook" >/dev/null 2>&1
  [[ $? -eq 2 ]] && got=block || got=allow
  if [[ $got != "$want" ]]; then echo "FAIL: expected $want: $cmd"; fail=1; fi
}

allowed=(
  'git push'
  'git push -u origin claude/x'
  'git push origin claude/x'
  'git push origin claude/force-fix'
  'git push -u origin claude/forced-push-guard'
  'git push origin HEAD:claude/x'
  'git status'
  'git commit -m "add guardrail"'
  'git commit -m "block git reset --hard"'
  'git merge origin/master'
  'git reset --soft HEAD~1'
  'git reset HEAD file.txt'
  'git branch -d claude/x'
  'git branch --list'
  'git branch -a'
  'git clean -n'
  'git clean --dry-run'
  'git checkout -b claude/force-x'
  'git log --oneline | head'
  'git diff && git push origin claude/x'
  'git -C /tmp/repo push origin claude/x'
  'git fetch origin master'
  'echo "git rebase is blocked"'
  'grep -r "git push --force" docs'
  'ls'
)
blocked=(
  'git push --force'
  'git push --force origin claude/x'
  'git push origin claude/x --force'
  'git push --force-with-lease'
  'git push --force-with-lease=claude/x origin claude/x'
  'git push --force-if-includes'
  'git push -f'
  'git push -f origin claude/x'
  'git push -uf origin claude/x'
  'git push origin +claude/x'
  'git push origin +HEAD:claude/x'
  'git push origin HEAD:+claude/x'
  'git reset --hard'
  'git reset --hard origin/master'
  'git rebase master'
  'git rebase -i HEAD~3'
  'git rebase --abort'
  'git commit --amend'
  'git commit --amend --no-edit'
  'git commit -m x --amend'
  'git filter-branch --tree-filter x'
  'git filter-repo --path x'
  'git clean -f'
  'git clean -fd'
  'git clean -df'
  'git clean --force'
  'git branch -D claude/x'
  'git branch -fD claude/x'
  'git branch --delete --force claude/x'
  'git status && git push --force'
  'git fetch; git reset --hard origin/master'
  'git diff | cat; git rebase master'
  'git -C /tmp/repo push --force'
  'git -c user.name=x rebase master'
  'FOO=1 git push -f'
  'sudo git reset --hard'
  '/usr/bin/git push -f'
  'bash -c "git push --force"'
  'sh -c "cd x && git reset --hard"'
  'echo ok
git rebase master'
)

for c in "${allowed[@]}"; do expect allow "$c"; done
for c in "${blocked[@]}"; do expect block "$c"; done
# Missing/empty command never blocks.
echo '{}' | "$hook" >/dev/null 2>&1 || { echo "FAIL: empty input blocked"; fail=1; }

if ((fail)); then exit 1; fi
echo "ok: ${#allowed[@]} allowed, ${#blocked[@]} blocked"
