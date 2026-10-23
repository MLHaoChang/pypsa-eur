# Gatekeeper: standing rules

What the gatekeeper applies to every review, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `GK OK @<full sha>: <what was checked>` or `GK NOT OK @<sha>: <what to fix>`. `scripts/coord/premerge.sh` reads them. Plan reviews go through the coordinator, because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last.

## Rules

- <how to set up a review checkout and run the tests, with timeouts>
- <how to review a delta after a base-branch merge>
- <known environment-only failures>
- <rulings made in earlier reviews>
