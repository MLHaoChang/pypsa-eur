# The other seventeen CodeQL alerts, triaged

*2026-09-12. Companion to `2026-09-12-path-injection-what-is-left-and-why.md`,
which covers the five `py/path-injection` alerts. Between them these two files
account for **all 22** alerts on `master`.*

Every alert below was read from the SARIF in the analysis job's own log —
rule, sink, source and the intermediate steps — because the code-scanning API
is not readable from the session that did the work. **The source matters as
much as the sink here, and it is the thing the alerts page never shows.**

## Summary

| rule | n | verdict |
|---|---|---|
| `py/clear-text-logging-sensitive-data` | 7 | 6 dev scripts that print keys on purpose; 1 name-based false positive |
| `py/insecure-temporary-file` | 4 | entirely inside test modules |
| `py/stack-trace-exposure` | 3 | typed or OS messages to an authorised principal about their own action |
| `py/clear-text-storage-sensitive-data` | 2 | **source is a test's planted key**; both sinks are redacted and test-pinned |
| `py/weak-sensitive-data-hashing` | 1 | sha256 of a 256-bit random token; **source is a test** |

**Seven of the seventeen have their source inside a test file.** That is not
visible from the alert list, and it is the single most useful thing this
exercise turned up: the "sensitive data" being tracked is, in those cases, a
fixture.

## `py/clear-text-storage-sensitive-data` (2)

Sinks `chat_service.py:1042` (the `chat.jsonl` append) and `:1109` (the
pending-turn write). **Both flows start at `tests/test_chat_e2e.py:2081`** — a
test's fake key.

Correction: they do NOT both run through `_run_turn_body`, as this document
first said. `append_turn` is called at `:4049`, inside `_run_turn_body` (def at
`:3576`); `begin_pending_turn` is called at `:3480`, inside `run_turn` (def at
`:3344`) and BEFORE `_run_turn_body` is invoked at `:3496`. The dispositions
below do not turn on the frame — both call sites wrap their content in
`_redact_for_persist` — but a document whose whole argument is "read the flow,
the source matters as much as the sink" does not get to be vague about the
frames in the middle.

Both are wrapped in `_redact_for_persist` at their call sites, and
`test_no_split_merge_precondition.py` proves it: it plants a key matching none
of the legacy patterns, drives a real turn, and asserts the value is absent
from **all three** of its sinks — two durable (`chat.jsonl` and the pending-turn
record) plus the backend LOG, which the test's own docstring keeps distinct and
which is not a durable project artifact — with a second test that disables the control
and asserts the value *does* leak, so the first passes because of the control
rather than because there was nothing to redact. The pending-turn sink was
added to that test in PR #9 on the strength of this very alert.

**Disposition: dismiss, false positive.** The redactor is not a barrier CodeQL
models.

## `py/clear-text-logging-sensitive-data` (7)

Six are development scripts whose entire purpose is printing the key being
investigated: `tools/auth_e2e_smoke.py` (1),
`smoke/regress_local_mode_api_key.py` (2), `smoke/repro_api_key_collision.py`
(3). They are not imported by the app and do not ship in a request path. The
last of those is literally a reproduction of a key-collision bug; printing both
colliding values is the reproduction.

The seventh is `routers/local_settings.py:136`, and it is a **name-based false
positive**:

```python
status, detail = probe_api_key()        # source, :132
...
logger.info("local settings: anthropic key updated, probe=%s", status)   # sink, :136
```

CodeQL treats anything returned by a function whose name contains `api_key` as
sensitive. What is logged is `status` — `"ok"`, `"invalid"`, `"cleared"` — and
the key itself is deliberately not in the line. The module is the one that
exists to keep keys out of logs.

**Disposition: dismiss — "used in tests"/"won't fix" for the six scripts, false
positive for the seventh.**

## `py/stack-trace-exposure` (3)

None is a traceback. Each is an exception *message* shown to a principal
entitled to see it, about an action they just took.

* **`admin.py:201`** — the `except email_service.EmailServiceError` is at
  `:195` and the f-string at `:197–200`; `:201` is the `return response` the
  scan named as the sink. It becomes `response["warning"] = "User created, but
  the set-password email was not sent: {exc}."`.
  **The "typed, app-authored exception" rationale does not hold and should not
  be used to dismiss this.** `EmailServiceError` has two subclasses and the
  argument only describes one: `EmailConfigurationError("SMTP is not
  configured")` is a fixed string, but `email_service.py:76` raises
  `EmailDeliveryError(f"SMTP delivery failed: {exc}")` from `except (OSError,
  smtplib.SMTPException)` — so `{exc}` can carry arbitrary smtplib/socket text:
  hostnames, ports, server banners, auth-failure detail. That is exactly what
  `py/stack-trace-exposure` is about.
  The disposition stands on the OTHER half of the argument — the audience. The
  route is admin-only (`tenancy_service._resolve_target_org_id` raises
  `PermissionDenied` otherwise), and an org admin configuring SMTP is entitled
  to their own server's error. If that audience ever widens, narrow the
  delivery message before anything else in this document.
* **`local_settings.py:195`** — `reveal_log` reports why opening the log file
  failed. Every route in that module is gated by
  `local_mode.reject_unless_local_mode`: desktop only, one user, their own
  machine, their own file. `log_path` is already in the response.
* **`projects.py:855`** — a per-directory failure reason from
  `legacy_import._stage_and_rename` (`f"{candidate.dir_name}: {exc}"` on an
  `OSError`) surfaced in the import report. Same desktop-only route as the
  folder importer: it tells the user which of *their* folders could not be
  imported and why.

Removing these would delete the diagnostic and leave a silent failure.

**Disposition: dismiss, won't fix.** If one is ever revisited, the change worth
making is narrowing `local_settings.py`'s bare `except Exception` to the OS
errors it actually expects — a tidiness improvement, not a disclosure fix.

## `py/insecure-temporary-file` (4)

`test_cost_totals_contract.py`, `test_myopic_build_period_visibility.py`,
`test_myopic_feasibility.py`, `test_myopic_horizon_cost.py`. No data flow at
all — the rule fires on the call shape. Test modules writing scratch files.

**Disposition: dismiss, used in tests.**

## `py/weak-sensitive-data-hashing` (1)

`auth_service.py:29` — `hashlib.sha256(raw_token.encode()).hexdigest()`, with
the flow starting at `tests/test_auth_service.py:96`.

The rule targets **passwords**, which need a slow KDF because they are
guessable. This hashes `secrets.token_urlsafe(32)` — 256 bits of entropy from a
CSPRNG. There is no dictionary to run against it, and a fast hash is the
correct construction for a bearer-token lookup key.

(Correction: `_hash_token` is not "the session token path". It has five callers
in `auth_service.py` — `:48`, `:67`, `:105` are the session path; `:146`
`issue_password_token` and `:162` `_claim_password_token` are the
password-reset / set-password `AuthToken` path. The disposition is unchanged
because both kinds are `secrets.token_urlsafe(32)`, and a user-chosen password
never reaches this function — those go through `hash_password` / pwdlib at
`:37` and `:214`. But "passwords are handled separately" was being carried by a
claim about the function that was not true of it.)

**Disposition: dismiss, false positive.**

## What would change these verdicts

Stated plainly so a future reader can check rather than trust:

* If `_redact_for_persist` is ever removed from either `chat_service` call
  site, the two storage alerts become real — and
  `test_no_split_merge_precondition.py` goes red first.
* If `local_settings.py` stops being gated by `reject_unless_local_mode`, its
  `:195` alert becomes real, because the principal is no longer the machine's
  owner. NOT `:136` — that one is dismissed above as a name-based false
  positive (only `status` is logged, one of five fixed literals), and removing
  a gate cannot make a false positive true. This bullet said "its two alerts"
  and so contained a trigger that could not fire, in the one section whose
  whole purpose is to let a reader check rather than trust.
* If `_hash_token` is ever pointed at a user-chosen password instead of a
  generated token, the hashing alert becomes real immediately.

None of those is a hypothetical worth pre-empting in code today; each is worth
knowing is the trigger.
