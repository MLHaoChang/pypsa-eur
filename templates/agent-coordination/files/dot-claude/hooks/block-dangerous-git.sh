#!/bin/bash
# PreToolUse hook on Bash: block git commands that rewrite history or destroy work.
# Adapted from mattpocock/skills `git-guardrails-claude-code`. Upstream blocks every
# `git push`; this repo allows a normal push and blocks only:
#   force-push (--force, --force-with-lease, -f, +refspec), reset --hard, rebase,
#   commit --amend, filter-branch / filter-repo, clean -f, branch -D.
# Reads the tool call as JSON on stdin; exit 2 blocks it and shows stderr to the agent.
# Matching is by token, not substring: `git push origin claude/force-fix` is allowed.
# Quoted text is not parsed, so a quoted `; git rebase` inside a message also blocks
# (fails safe).

reason=""

# Flag cluster: a short-option group that contains letter $1 (-f, -fd, -uf).
has_short() { # has_short <letter> <token>
  [[ $2 =~ ^-[a-zA-Z]+$ && $2 == *"$1"* ]]
}

# check_git <subcommand> <args...>: sets $reason when the call is forbidden.
check_git() {
  local sub=$1 a; shift
  case "$sub" in
    push)
      for a in "$@"; do
        if [[ $a == --force* || $a == "+"* || $a == *:"+"* ]] || has_short f "$a"; then
          reason="force-push"; return
        fi
      done ;;
    reset)
      for a in "$@"; do [[ $a == --hard ]] && { reason="reset --hard"; return; }; done ;;
    rebase) reason="rebase" ;;
    commit)
      for a in "$@"; do [[ $a == --amend || $a == --amend=* ]] && { reason="commit --amend"; return; }; done ;;
    filter-branch | filter-repo) reason="$sub" ;;
    clean)
      for a in "$@"; do
        if [[ $a == --force ]] || has_short f "$a"; then reason="clean -f"; return; fi
      done ;;
    branch)
      local del=0 force=0
      for a in "$@"; do
        if [[ $a == -D ]] || has_short D "$a"; then reason="branch -D"; return; fi
        [[ $a == --delete ]] && del=1
        [[ $a == --force ]] && force=1
      done
      ((del && force)) && reason="branch --delete --force" ;;
  esac
}

# check_command <text>: split into simple commands, find each `git`.
check_command() {
  local text=$1 seg tok i
  while IFS= read -r seg; do
    local -a w=()
    read -ra w <<<"$seg"
    i=0
    # Skip env assignments and wrappers in front of the command.
    while ((i < ${#w[@]})); do
      tok=${w[i]}
      if [[ $tok == *=* && $tok != -* ]] || [[ $tok =~ ^(sudo|env|command|time|nohup|exec|xargs|!|\{)$ ]]; then
        ((i++))
      else
        break
      fi
    done
    ((i < ${#w[@]})) || continue
    tok=${w[i]##*/}
    if [[ $tok =~ ^(bash|sh|zsh)$ ]]; then
      # sh -c "<command>": inspect the payload.
      for ((j = i + 1; j < ${#w[@]}; j++)); do
        if [[ ${w[j]} == -c || ${w[j]} =~ ^-[a-z]*c$ ]]; then
          check_command "${w[*]:j+1}"
          break
        fi
      done
      [[ -n $reason ]] && return
      continue
    fi
    [[ $tok == git ]] || continue
    # Skip git's global options to reach the subcommand.
    ((i++))
    while ((i < ${#w[@]})); do
      case "${w[i]}" in
        -C | -c | --git-dir | --work-tree | --namespace) ((i += 2)) ;;
        -*) ((i++)) ;;
        *) break ;;
      esac
    done
    ((i < ${#w[@]})) || continue
    check_git "${w[i]}" "${w[@]:i+1}"
    [[ -n $reason ]] && return
  done < <(printf '%s\n' "$text" | tr -d "\"'" | sed -E 's/&&|\|\||[;|&(){}`]|\$\(/\n/g')
}

INPUT=$(cat)
COMMAND=$(jq -r '.tool_input.command // empty' <<<"$INPUT")
[[ -n $COMMAND ]] || exit 0

check_command "$COMMAND"
if [[ -n $reason ]]; then
  echo "BLOCKED ($reason): '$COMMAND'. Never force-push or rewrite history in this repo; a normal 'git push' is fine. Use a merge commit or a new commit instead, and ask the coordinator if you think this is needed." >&2
  exit 2
fi
exit 0
