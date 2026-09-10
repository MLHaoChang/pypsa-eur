# A project-lock refusal sends a collaborator's email to the LLM provider

**Date:** 2026-08-27
**Found on:** `master` at `499c2e02`, during the merged-tree security pass over
the provider seam × project-write-safety interaction.
**Severity:** Medium. **Affects:** server / multi-tenant deployments only.
**Status: OPEN and UNOWNED at time of writing.** The two sessions that
understood the halves (the write-safety line and the provider seam) both ended.
Written down here precisely so it does not evaporate with them.

## What happens

A write-tier chat tool hits a project held by another user. The lock layer
refuses with an `HTTPException` whose `detail` carries:

```python
{"error_kind": "project_locked",
 "message": "'<project>' is being edited by another user.",
 "lock": {"holder_email": <email>, "yours": False}}
```

`services/project_locks.py:133` builds `holder_email`. The chat tool-error path
then does, at `services/chat_service.py:3123`:

```python
"content": str(detail or exc)[:1000],
```

So the whole dict — **including the other user's email address** — becomes the
`tool_result` content block. That block is appended to the outbound message
array and to `session.messages`, which means it is:

1. sent to the third-party LLM provider as part of the conversation,
2. **replayed on every subsequent turn of that session**, and
3. written into `chat.jsonl` if the model echoes it back in its own reply text
   (agents routinely paraphrase a tool error to the user — "that project is
   locked by alice@…").

## Why redaction is not the fix

`_redact_for_persist` / `redact_secrets_in_str` are **secrets-only by design**,
and their docstring says so explicitly: bare email addresses are *intentionally*
not redacted, because that pattern over-redacts legitimate project and component
names. Pointing the secret-scrubber at PII would be the wrong repair — it would
degrade the transcript everywhere to patch one payload.

Note also that the redaction net is not even on this path: live SSE frames and
in-memory `session.messages` are deliberately unredacted
(`chat_service.py:335-336`); only the on-disk record is scrubbed.

**The fix belongs where the payload is built** — the lock layer should hand the
chat surface something the model may see (`"locked by another user"`, or an
opaque holder token), and keep the email for the HTTP/UI surface that already
displays it.

## Classification — this is not a credential leak

Getting this right matters for triage, because the imprecise version ("email
leak to the LLM") invites either panic or dismissal and both are wrong:

* It is an **identifier** disclosure, not a secret. No credential, token, or DB
  error is exposed — `serialize_lock` swallows DB exceptions to `None` rather
  than surfacing them.
* The recipient is an **already-authorised collaborator** on that same project;
  `resolve_project` runs before any lock check, so a cross-tenant stranger
  cannot reach the payload. The UI already shows the same `holder_email` in its
  lock banner.
* What is genuinely new is that the identifier now **leaves the deployment** —
  into a third-party API payload, into session replay, and potentially into an
  exported transcript.

## Why the desktop app is exempt

`local_mode.is_local_mode()` short-circuits `_check_foreign_lock`
(`services/chat_tools.py:3684`) and the router-side lock predicates before the
refusal payload is ever constructed. The packaged app launches with
`PYPSAGUI_LOCAL_MODE=1`, one identity, no lock semantics — so the payload does
not exist there. Independently verified by a second session.

## Not verified

* Whether an export/share-transcript feature would carry this beyond the
  project's own collaborators.
* Other `HTTPException` raise sites with similarly-shaped `detail` dicts outside
  the two lock predicates — the pass was scoped to the lock layer.
