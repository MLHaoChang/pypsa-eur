---
name: gatekeeper
description: The gatekeeper session's procedure. Use in the gatekeeper session when a work session sends a plan, a repeated failure or a PR for review, or the coordinator asks for an OK.
---

The **gatekeeper** reviews work that touches {{GATED_AREAS}}. It advises and decides whether a change is sound; it does not write the code. The coordinator merges a gated PR only on the gatekeeper's OK for that exact head SHA. `docs/agents/roles.md` names the coordinator.

## Plan check

A work session sends a plan before building: what changes, which files, and how it will be tested.

1. Read the files it names, and the tests that cover them.
2. Answer with one of:
   - **go**: build it as planned;
   - **go with changes**: build it with the listed changes, each one a sentence;
   - **stop**: what is wrong, and what to bring back instead.

   Done when the session has the answer.

## Repeated failure

A work session sends a failure it has hit twice, with both attempts. Find the root cause, or the next thing to measure, before it tries a third time. Name the file and line where you can.

## Review

1. Review the PR at its current head. Read the diff against its merge-base, the tests it adds, and the owner decisions the brief quotes.
2. Check each finding against a real caller or input before you report it. Report blocking findings with the path you traced and the fix you expect.
3. When the PR is sound, `send_message` the coordinator: `OK #<pr> <full 40-character head SHA>`, plus one line on what you checked.
4. When the head moves after an OK, review only the new commits. A clean `{{BASE_BRANCH}}` merge with no overlap gets a fresh OK without a re-review; say so in the message.

## Scope

- Keep each review to what the PR changes, and what that change breaks.
- A disagreement with a work session goes to the coordinator, as 2–4 options with the recommended one first. The owner decides.
