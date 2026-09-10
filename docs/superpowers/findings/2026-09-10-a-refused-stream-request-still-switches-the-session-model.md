# A refused `/stream` request has already switched the session's model

**Date:** 2026-09-10
**Found while:** checking a claim in
`2026-09-09-chat-stream-loop-two-vestigial-guards.md` before acting on it — the
claim was wrong, and this is what checking it turned up
**Status:** open, NOT fixed. The fix is a design decision about where a guard
belongs, not a one-liner; a proposal is at the bottom.

## The sequence

`POST /api/chat/stream` applies a per-turn model switch in the ROUTE:

```python
# routers/chat.py:563
if body.model:
    session.model = body.model
```

The concurrency guard that refuses a second turn on the same session lives
further in, inside `run_turn`:

```python
# services/chat_service.py:2746
if session._turn_in_flight:
    yield "error", {"error_kind": "turn_already_in_flight", ...}
    yield "session_done", {"reason": "turn_already_in_flight"}
    return
```

So for a second `/stream` arriving on a session whose turn is still running:

1. the route sets `session.model` to whatever the new request asked for;
2. `run_turn` then refuses the request with `turn_already_in_flight`.

**The refused request has already mutated the running turn's state.** And that
state is read, not cached: PR #8's retry loop re-reads `request.model` from
`session.model` at the top of every attempt (its docstring says so explicitly —
that re-read is what makes the Opus→Sonnet fallback reach the wire). So the
in-flight turn can change model between attempts because of a request that was
rejected.

## Why it is narrow, stated plainly

The window is between two attempts of the SAME turn, which only opens when the
first attempt failed transiently — a rate-limit or an upstream error. On the
happy path a turn makes one attempt and there is nothing to switch. So this
needs a retrying turn AND a concurrent `/stream` naming a different model. Two
tabs on one session under provider load, or a UI model-picker click during a
slow turn.

The consequences are cost and consistency rather than corruption: half a turn
answered by one model and half by another, or a turn silently upgraded to Opus
after the fallback deliberately downgraded it. `model_fallback_used` is what
stops the downgrade looping — which is the correction the other finding records.

## Not a test gap that can be closed as-is

Worth noting for whoever picks this up: no single-threaded test can exercise it.
The mutation `model_fallback_used = False`-per-attempt passes the existing suite
precisely because nothing in the suite puts the model back to Opus mid-turn. A
test for this needs a second request issued while the first turn is between
attempts — which the existing `test_chat_sse.py` threading harness could
actually express, since it already drives an SSE turn from a thread while the
test body issues other requests.

## What a fix would be

The switch should happen where the in-flight claim happens, so that a refused
request has no effect:

* **Move it into `run_turn`**, after the `_turn_in_flight` claim succeeds — the
  session is then known to be idle and the mutation is under the same guard.
  Needs the requested model threaded through as a parameter rather than read off
  the session, which is a small signature change to a heavily-called path.
* **Or guard it in the route** (`if body.model and not session._turn_in_flight:`)
  — one line, but it reaches for a private attribute and is a check-then-act
  race in its own right: the turn can start between the test and the assignment.
  Narrower than today's behaviour, still not correct.

The first is right and the second is a mitigation. Which to take depends on how
much churn that signature is worth, so it is left to whoever owns the chat
layer rather than decided here.
