# Two real CodeQL alerts, neither introduced by the decomposition

**Date:** 2026-09-08
**Found by:** the `CodeQL` check on PR #6, head `23efc6c` — *"2 new alerts
including 1 high severity security vulnerability"*
**Status:** open. Recorded, not fixed here — this branch's contract is
strictly behaviour-preserving, and both fixes change observable behaviour.

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
