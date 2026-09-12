# Auditing a merge into a moving master

`master` moved **five times** between 2026-09-10 and 2026-09-12 under one
long-lived branch. Every merge was resolved carefully, every merge passed the
suite, and **three of them shipped a defect the suite could not see**, because
a merge can delete work without breaking anything that remains.

This is the check for that, and the argument for why a green suite is not it.

    python pypsa-gui/backend/tools/merge_audit.py --theirs origin/master

Run it after resolving and **before committing**. `--merged` defaults to the
working tree, which is the point. Exit 0 clean, 1 findings, 2 could not run.

## The three incidents it is built from

**A function reverted to the merge-base copy.** The decomposition merge left
the tree holding the base version of `with_periodized_cost_defaults` — the
branch's `for_back_calculation` edit was simply gone. Every capital-cost figure
in the app read EUR 0.00. It surfaced as 70 test failures from one root, and
only because some test happened to assert a number; nothing structural noticed
that a function had quietly rewound. A per-function audit of all 122 changed
functions found this and **three more**.

**A security control deleted by the next hunk's resolution.** The C-1
capability guard (`tool_not_offered`, refusing inbound `tool_use` blocks a turn
never offered) was ported into a conflict block. The following hunk's
resolution replaced that block with master's call — taking the guard with it.
Every seam test still passed. What caught it was a test that watched the tool
actually get *dispatched*, which is a different question from whether the
frames look right.

**Duplicate top-level definitions, and a half-spliced function.** Two modules
ended up defining the same name twice, byte-identically, one silently shadowing
the other — found only by an AST scan, after a `git grep` against a rev had
returned empty for a symbol that was present and sent the resolution down the
wrong path. Separately, taking `bulk_update` hunk-by-hunk left it spliced
against auto-merged regions still using the old variable names: nine undefined
names, caught by `ruff --select F821`, not by any test.

The shape they share: **the merged tree is internally consistent and passes,
while something a parent had is silently absent.** Tests assert what the code
does. They do not assert that it still does everything it used to.

## What the three checks are

| check | question |
|---|---|
| lost edits | did a function this branch changed come out of the merge holding the *merge-base* copy? |
| duplicate definitions | does any module define one name twice at top level? |
| vocabulary | is a definition or literal either parent had missing from the result? |

`--inventory REGEX` extends the third with a domain vocabulary, repeatable.
Two that have actually mattered here:

```bash
python pypsa-gui/backend/tools/merge_audit.py --theirs origin/master \
    --inventory '"error_kind":\s*"(\w+)"' \
    --inventory 'yield\s+"(\w+)"'
```

Both were dropped by a merge in practice — an `error_kind` the frontend routes
on, and an SSE frame name. Neither has a test that enumerates the set.

## What it cannot do, measured

**These numbers are from running it on the real merges, not from intent.** On
`1c7b86d8` — the hardest, 23 conflicted files, 78 hunks — the top-severity
class reported 4 findings, of which **2 were false positives**:

* `routers/results.py::get_cost_breakdown` and `::get_asset_economics`
  reverted to the base copy *in that file* because the decomposition moved the
  body into `services/results/cost_breakdown.py` — and **renamed it** to
  `compute_cost_breakdown`. The relocation allowance matches by NAME, so a
  rename-on-move defeats it. This is the repository's own dominant refactoring
  pattern, so expect it.

The other two were genuine supersessions worth the look (`conftest.make_auth_db`,
where master's parameterised version was deliberately taken). `07a3d7df`
reported 0 in that class. Duplicate definitions reported 0 on all three merges
— correctly, because they had been fixed before commit.

So: **a handful of places to look on a large merge, roughly half of them real.**
That is worth thirty seconds. It is not a gate, and it must not become one.

Three limitations to keep in mind:

1. **Rename-on-move is invisible.** See above. If the decomposition renamed as
   it moved, confirm by content, not by this tool.
2. **It proves nothing about behaviour.** It is structural. A function that
   survived the merge with its logic mangled looks identical to one that
   survived intact.
3. **A finding is a place to look, not a verdict.** A function you deliberately
   reverted to the base version is a finding too, and should be.

## Why it self-tests

    python pypsa-gui/backend/tools/merge_audit.py --self-test

Plants each defect class in a throwaway repository and asserts the check fires
— then asserts a *clean* merge of the same shape reports nothing. Both halves
matter, and the same reasoning as `test_no_split_merge_precondition.py`: a
check that cannot fail makes the audit a formality, and a formality trains the
next person to skip it.

It has already earned its keep. Widening the name-extraction to count imports
(needed so a function turned into a re-export is not reported missing) silently
broke the duplicate check — from 0 findings to 14, all noise, because importing
a name twice is ordinary. The self-test and a re-measurement on real merges
caught it; reading the code did not.

## The order to work in

1. Resolve, keeping master's *structure* and porting this branch's edits into
   it. Never restore a moved body to its old home.
2. `ruff check --select F821,F811` — undefined names and redefinitions, which
   is what a half-spliced function looks like.
3. `merge_audit.py` — and read the findings rather than counting them.
4. The suite, then the QA drivers. `gui-qa-drivers` is a separate CI step from
   `gui-tests` and covers direct handler calls the pytest suite does not; a
   merge broke exactly that and only that.
5. Only then commit.
