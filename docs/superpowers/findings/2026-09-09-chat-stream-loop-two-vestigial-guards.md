# Two vestigial pieces in the chat stream-retry loop

**Date:** 2026-09-09
**Found while:** Phase D of
`docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md` — extracting
`_stream_assistant_message` from `services/chat_service.py::_run_turn_body`
**Status:** open. Neither is a malfunction; both are recorded rather than fixed
because that phase's contract is strictly behaviour-preserving.

## 1. `pending_blocks` is written and never read

The retry loop accumulates every completed content block:

```python
pending_blocks: list[dict[str, Any]] = []
...
elif etype == "content_block_stop":
    block = getattr(event, "content_block", None)
    if block is not None:
        pending_blocks.append(_serialise_for_anthropic(block))
```

and its comment says why:

> We accumulate content blocks locally so we can replay them as a single
> assistant message back into the SDK on the next turn (tool-use convention).

That replay is real, and it does not use this list. Twenty lines later:

```python
assistant_blocks = [
    _serialise_for_anthropic(b)
    for b in getattr(final_message, "content", []) or []
]
messages.append({"role": "assistant", "content": assistant_blocks})
```

So the intent was superseded by reading `final_message.content`, and the local
accumulation was left behind. `grep` confirms `pending_blocks` has exactly two
mentions — the initialisation and the append — and no reads.

**Why it is not merely untidy.** The two would differ if the SDK's
`get_final_message()` ever omitted a block that streamed, or ordered blocks
differently from the stream. Whichever is authoritative should be the one used;
right now the code computes both and silently discards the one whose comment
explains the requirement. A reader trying to establish which is correct has to
notice the discard first.

**What a fix would be.** Delete the accumulation, and move that comment onto
`assistant_blocks` where the behaviour actually lives — or, if the stream is the
authoritative order, use `pending_blocks` and delete the rebuild. That is a
decision about which source to trust, not a tidy-up, which is why it is a
finding rather than part of the refactor. Phase D returns the list on its
outcome object so the choice stays visible rather than being quietly dropped.

## 2. `model_fallback_used` is redundant with the model check

The Opus→Sonnet escape hatch is guarded twice:

```python
if (
    error_kind == "rate_limited"
    and not emitted_this_attempt
    and session.model == OPUS_MODEL
    and not model_fallback_used
    ...
):
    model_fallback_used = True
    session.model = DEFAULT_MODEL
```

The flag can never be the deciding condition. The same branch sets
`session.model = DEFAULT_MODEL`, and nothing anywhere returns it to `OPUS_MODEL`
mid-turn, so `session.model == OPUS_MODEL` is already false on any second pass.

**Found by a mutation that failed to fail.** Phase D's guard
`test_the_fallback_is_granted_at_most_once` was mutation-tested by resetting
`model_fallback_used = False` on every attempt — and the test still passed. The
first reading of that is "the guard is vacuous"; the correct reading is that the
property still holds, because one of two independent guards was removed and the
other carries it alone.

Recorded because the pair is now misleading in both directions: a reader may
believe the flag is load-bearing, and anyone mutation-testing this code will hit
the same non-result and may wrongly conclude the test is worthless. Removing the
flag, or keeping it with a comment saying it is belt-and-braces, both beat the
current silence. Not done here: it is dead-code removal in a
behaviour-preserving phase.

---

# Addendum, 2026-09-10: a third one, and this one was a TEST

Same family, found while extracting `_check_save_allowed` from
`routers/projects.py::_save_context`.

`tests/test_chat_state_carry.py::test_save_guard_source_placement_invariant`
asserted the v6 F1 TOCTOU invariant — the cross-project 409 sits inside
`with ctx.mutation_lock:` and after the `loaded = ctx.loaded_project` read — by
reading `routers/projects.py` as TEXT and comparing the line numbers of four
anchor strings. `line_with` returned the FIRST match anywhere in the file.

After the extraction it still passed, and its four anchors were:

```
lock         1389   in _check_save_allowed   (the COMMENT "# the `with ctx.mutation_lock:` block opened above, AFTER the")
loaded_read  1390   in _check_save_allowed   (the COMMENT "# `loaded = ctx.loaded_project` read above.")
guard        1403   in _check_save_allowed
empty_check  1418   in _check_save_allowed
```

The first two matched **the comment describing the invariant**, which travelled
with the guards. The function it was inspecting contains no
`with ctx.mutation_lock:` statement at all. The test would have stayed green
through a refactor that genuinely moved the guard out of the lock, provided the
comment moved with it.

This is the second time in this work that a guard passed by matching a comment
rather than the thing it names — the first was the `pytest.ini` collection guard
(`docs/superpowers/findings/2026-09-05-qa-driver-rot.md`), which matched
`python_files = test_*.py` in a comment instead of the setting. The shape is
worth naming: **a source-text assertion whose anchor also appears in prose about
itself is self-satisfying.** Docstrings and comments are exactly where such
prose lives, and a careful author is likely to write it — so the better the
comment, the more likely the guard is vacuous.

Replaced by `tests/test_save_guards_seam.py`, which asserts the property by
observation: a recording lock whose `__exit__` must receive the HTTPException,
which is direct proof the raise happened inside it. Both old and new catch a
hoist of the guard above the lock BEFORE the extraction; only the new one still
catches it after.
