# UX reviewer: standing rules

Only for a project with a UX reviewer. What it applies to every UI PR, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `UX OK @<full sha>: <what was checked>` or `UX NOT OK @<sha>: <what to fix>`. `scripts/coord/premerge.sh` reads them.

## Rules

- <navigation, wording and layout rules>
- A base-branch merge carries an OK when the head tree equals `git merge-tree --write-tree <old> <base>`; any difference needs a real look.
