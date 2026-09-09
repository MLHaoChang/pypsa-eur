# Two real CodeQL alerts, neither introduced by the decomposition

**Date:** 2026-09-08
**Found by:** the `CodeQL` check on PR #6, head `23efc6c` — *"2 new alerts
including 1 high severity security vulnerability"*
**Status:** FIXED 2026-09-09 on `claude/fix-codeql-tempfile-and-trace-exposure`,
branched off `master` — not here, because this branch's contract is strictly
behaviour-preserving and both fixes change observable behaviour. See "How they
were fixed" at the bottom, which also records a merge hazard this creates.

## What the check reported

| severity | rule | location on the branch |
|---|---|---|
| high | `py/insecure-temporary-file` | `services/solver_service.py:382` |
| medium | `py/stack-trace-exposure` | `routers/results.py:275-278` |

## Neither is new

CodeQL says so itself, in the check's own summary: *"Alerts not introduced by
this pull request might have been detected because the code changes were too
large."* This PR decomposes four god files, so a great deal of code changed
line number and enclosing file without changing at all. Verified for both:

**`tempfile.mktemp`** — `git show master:…/solver_service.py` has the identical
line at 701:

```python
tmp_log = pathlib.Path(tempfile.mktemp(suffix=".log"))
```

The decomposition moved it to line 382 of the now-smaller file. Same call, same
arguments. (Four test modules call `mktemp` the same way; CodeQL did not flag
those, presumably because they are not reachable from a request.)

**The stack trace in a response** — `master:…/routers/results.py:857` ends
`get_economics_by_carrier` with:

```python
except Exception as exc:
    import traceback
    return {"error": str(exc), "trace": traceback.format_exc().splitlines()[-5:]}
```

The decomposition moved that verbatim into
`services/results/economics_by_carrier.py:45`; the route now returns whatever
that function returns, which is the same dict it returned before.

So the correct reading is: the branch is not where these came from, and merging
it does not make the product less safe than master. It is where they became
*visible*, which is worth something on its own.

## Both are worth fixing anyway

**`tempfile.mktemp` (high).** The classic CWE-377 race: `mktemp` returns a name
that is free *right now* and the caller opens it later, so anything that can
write to the temp directory can win the gap — symlink the path at a file it
wants overwritten with solver output, or pre-create it and read the solver log.
On a single-user desktop install this is close to theoretical; on the shared
web deployment the tenancy migration was built for, `/tmp` is shared by every
tenant's process.

The fix is `tempfile.mkstemp(suffix=".log")` (take the fd, close it, keep the
path) or a `mkdtemp()` directory with a fixed name inside it. Both change the
file's *permissions* — 0600 rather than the umask default — which is why it is
not a drive-by on a behaviour-preserving branch: whatever reads that log
afterwards has to still be able to.

**The stack trace (medium).** `GET /api/results/economics_by_carrier` answers
`200` with the last five frames of a traceback whenever the computation raises.
That leaks absolute paths, module layout and library versions to any signed-in
caller, and the response shape is a lie besides — a caller checking for the
documented `{"by_carrier": …}` payload sees a `200` and a dict.

The fix is to log the traceback and return an error the client can act on
(`500`, or `{"error": "…"}` with no trace). That is a behaviour change to a
route the Results tab consumes, so it needs to be made deliberately: the
frontend's current handling of the `error`/`trace` keys has to be checked
first.

## Why the check will stay red until then

CodeQL alerts are deterministic — a re-run reanalyses the same code and reports
the same two alerts. There is no flake to wait out and nothing to port from
master, because master is where they already live. The check goes green when
the two are fixed or dismissed, and the fixes belong in a PR whose contract
allows a behaviour change, not in this one.

---

# How they were fixed, 2026-09-09

On `claude/fix-codeql-tempfile-and-trace-exposure`, off `master`, because that
is where both defects live.

## `tempfile.mktemp` → a private directory

`mkstemp` was not available as a fix: pypsa passes `log_fn` down to the solver
as a NAME and the solver opens it itself, so there is no descriptor to hand
anyone and the file has to be creatable later by design. What must not be
creatable by anyone else is the directory it sits in.

`_make_solve_log_path()` returns `mkdtemp()/solve.log` plus its cleanup
callable. The directory is 0700 from the moment it exists, so the log file's own
mode stops mattering; one directory per solve, so two concurrent solves cannot
interleave into one log; and `rmtree` replaces the old `tmp_log.unlink()`, which
would now leave the directory behind.

The permissions worry in the write-up above turned out not to apply. It assumed
`mkstemp`'s 0600 on the FILE; a private directory changes no mode any legitimate
reader depends on, and nothing outside the process reads that file at all — the
tail thread streams it to the UI, the solver writes it, and it is deleted when
the solve ends.

## The traceback → the log

The graceful degradation stays (one bad carrier must not blank the Results tab);
`logger.exception` takes the detail. `str(exc)` went too — it was the second
flow CodeQL reported on that line, and a `FileNotFoundError` renders as its
path.

Checked before changing a route the Results tab consumes, which the write-up
above said had to be done first: `frontend/src/api/simulation.ts` types the
response as `error?: string` with **no `trace`**, and both readers
(`pages/results/Dispatch.tsx`, `pages/results/CapacityExpansion.tsx`) take
`econByCarrier?.by_carrier ?? null`. Nothing in the frontend reads either key.

## The merge hazard this creates

Worth stating plainly, because it is the cost of fixing these off `master`
while this branch is open: **this branch MOVES both fixed lines.** The
traceback return is at `routers/results.py:857` on `master` and at
`services/results/economics_by_carrier.py:45` here. So merging this branch after
the fix lands would delete the fixed copy and keep this branch's unfixed one —
a silent reintroduction, not a conflict, because the two sides touch different
files.

**Closed by porting**, which is the alternative that removes the hazard rather
than documenting it: both fixes are now on this branch too, as their own
clearly-labelled commit, applied at this branch's locations (the traceback fix
in `services/results/economics_by_carrier.py`, where the move put it). The port
no-ops once `master` carries the same change, there is no longer an unfixed copy
for a merge to prefer, and this PR's CodeQL check goes green.

That is a behaviour change on a branch whose contract is behaviour-preserving,
and it is a deliberate exception rather than a loosening of the contract: the
change is already written, reviewed and tested on its own branch, it is labelled
as a port rather than folded into a refactor commit, and the alternative was to
leave a known silent-regression path open across a merge. A defect found FROM
HERE still goes to a finding and a separate branch — that is what happened.

## Verification

Both tripwires written first, red for the right reasons, and mutation-checked
after: a 0755 temp directory and a `logger.error` without `exc_info` each make
them fail. The `mktemp` guard reads the AST rather than the file text, so the
docstring explaining why `mktemp` is not used cannot satisfy it.

A real solve through `run_simulation`: `ok`/`optimal`, 49 log lines captured
including HiGHS output — so the tail thread does read what the solver wrote into
the private directory — and no leftover `pypsagui-solve-*` directories. Full
suite failing set unchanged at 2, both pre-existing on `master` (the macOS-only
assertions in `test_app_paths.py`, which this branch already fixed and `master`
has not).
