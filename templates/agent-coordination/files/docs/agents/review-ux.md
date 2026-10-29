# UX reviewer: standing rules

Only for a project with a UX reviewer. What it applies to every UI PR, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `UX OK @<full sha>: <what was checked>` or `UX NOT OK @<sha>: <what to fix>`. `scripts/coord/premerge.sh` reads them. The verdict must be the first line of the comment; the attribution footer goes last. The PR comment is the verdict. Message the coordinator only for a NO-GO the builder disputes, a NOT OK that blocks a merge, or a standing ruling to record. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it.

## Rules

- Read a PR with `git diff --stat` first, then one hunk or file at a time, each capped with `| head -c 6000`. Whole-file or cross-file reads go to an Explore subagent that returns file:line findings only.
- Open screenshots downscaled: resize to at most 1000 px wide before `Read` (13 full PNGs cost ~15k tokens).
- <navigation, wording and layout rules>
- A base-branch merge carries an OK when the head tree equals `git merge-tree --write-tree <old> <base>`; any difference needs a real look.
