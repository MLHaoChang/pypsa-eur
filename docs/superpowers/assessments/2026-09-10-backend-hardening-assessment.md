# Backend hardening assessment

Date: 2026-09-10
Scope: `pypsa-gui/backend` at `5f9131e` (master).
Method: read the code at each trust boundary and check the claim the code
makes about itself against what it actually does. Every line reference below
was opened and read; nothing here is inferred from naming.

This is an assessment, not a change. One finding was written up separately
(`docs/superpowers/findings/2026-09-10-a-tool-result-can-close-the-untrusted-fence.md`);
nothing else here has been fixed, and the ranking below is my
recommendation for what to fix first, not a record of work done.

## Calibration: what is already solid

Reviewers should not re-litigate these. They were checked and they hold.

- **Deserialisation is genuinely restricted, not nominally.**
  `routers/projects.py:280-310` defines `_SAFE_UNPICKLE_GLOBALS` as an
  allowlist of exact `(module, name)` pairs and refuses everything else. The
  exactness matters: a prefix allowlist over `pandas` would re-admit
  `pandas.read_pickle` and `pandas.eval`, which is a full bypass. The
  restricted loader is used at *every* read site, not just the obvious one.
  `undo_service.py:155` does call bare `pickle.loads`, but only on payloads
  it itself pushed onto an in-memory stack in the same process — no
  attacker-reachable path reaches it.
- **Credential and session handling uses the right primitives.**
  `services/auth_service.py:15` uses `PasswordHash.recommended()` (argon2)
  rather than a hand-rolled hash; session tokens are
  `secrets.token_urlsafe(32)` (`:30`) and are stored as
  `hashlib.sha256(...).hexdigest()` (`:29`), so the DB holds no usable
  bearer value. Session cookies are `httponly=True`.
- **There is a real global auth gate, and it runs early.**
  `main.py:658-702` refuses any non-OPTIONS `/api/*` request outside
  `_AUTH_PUBLIC_PATHS` with a 401 before the endpoint is reached, and
  `main.py:645-657` does the CSRF Origin/Referer + double-submit check
  *before* session resolution, so a forged request is refused without
  touching the database. A DB outage degrades to 503 with an actionable
  message rather than a blanket 500.
- **Tenant isolation prefers 404 over 403.** The project/org lookups return
  not-found rather than forbidden for another tenant's resource, so the API
  does not confirm existence to a non-member. That is the right default and
  it is applied consistently.
- **No raw SQL string building.** Queries go through SQLAlchemy constructs;
  I found no f-string or `%`-formatted SQL anywhere in `routers/` or
  `services/`.
- **Upload limits are explicit and bounded.** `services/upload_service.py`
  caps `MAX_FILE_BYTES = 25 MiB`, `MAX_MULTIMODAL_IMAGE_BYTES = 10 MiB`,
  `MAX_MULTIMODAL_BLOCKS = 20`, validates MIME against `ALLOWED_MIME_TYPES`,
  and constrains identifiers with `_FILE_ID_RE = r"\A[0-9a-f]{16}\Z"` — an
  anchored character class, so no path traversal through a file id.
- **Prompt injection is treated as a real threat, not ignored.** The
  `<untrusted_data>` fence plus its system-prompt clause exists precisely
  because tool output is attacker-influenced. Gap 1 below is a hole in that
  mechanism, which is a different (and much better) situation than not
  having thought about it.

## Ranked gaps

### 1. The untrusted-data fence is bypassable on the tool-result path

`services/chat_service.py:3647-3678` (`_result_to_anthropic_content`) ends:

```python
return f"{_UNTRUSTED_OPEN}\n{body}\n{_UNTRUSTED_CLOSE}"
```

It wraps without first removing delimiters already present in `body`.
`_sanitise_ui_value` (~line 1744) on the UI path does strip them:

```python
text = text.replace(_UNTRUSTED_OPEN, "").replace(_UNTRUSTED_CLOSE, "")
```

So the same defence is applied on one path and skipped on the other. I
demonstrated this live: a bus whose *name* contains the closing delimiter
produces model-facing content with two closing delimiters, and it survives
`json.dumps` intact — JSON escapes `"` and newlines, but not `<` or `/`.
Anything after the injected close reads to the model as trusted instruction
text rather than as data.

Blast radius is bounded, and honestly so: the safety tiers still stand
between a persuaded model and damage — `DESTRUCTIVE_TIERS` require an
explicit confirmation round-trip, and `AUTO_APPROVE_TIERS` is empty by
default. What an injection buys is the read tier and whatever the operator
opted into, plus the ability to shape what the user is *told* — which is
enough to matter, since the confirmation prompt itself is model-authored
text. Fix is one line (strip before wrapping, mirroring the UI path); two
decisions are open (strip vs. escape, and whether the deliberately
unwrapped `is_error` path needs the same treatment). Full write-up in the
finding.

### 2. The `exec()` gate's stated justification is now false

**FIXED 2026-09-12.** `routers/simulation._gate_user_code` now refuses a
non-admin's attempt to set `extra_functionality_code`, at the edge and before
the merge that would store it, and the docstring below is corrected. Both
conditions are required: admin does not override the operator opt-in. The
2026-09-12 per-route authorization audit (finding 4) also REFINES the claim
below: `/api/simulation/` is under the foreign-lock middleware, so a
non-holder was already refused; the escalation was member-with-the-lock to
in-process RCE, not any authenticated user. Analysis below unchanged.

`services/solver_service.py:1446-1456`, `user_code_enabled()`:

> Off by default — this field is `exec()`-ed in-process with full FS /
> network privileges, **and the GUI has no auth layer**, so allowing it
> implicitly is a footgun. […] enable for trusted **single-user /
> localhost** deployments.

Both italicised claims were true when written and are not true now. There
*is* an auth layer (`main.py:658`), and the product is multi-tenant with
orgs and memberships (`routers/admin.py`). The gate itself is fine; its
reasoning is stale, and stale reasoning around an `exec()` is how a gate
gets relaxed by someone who reads the docstring and concludes the risk was
"no auth", which is now solved.

What the current shape actually is:

- The flag is a **process-wide environment variable**
  (`PYPSA_GUI_ALLOW_USER_CODE`). It cannot be granted to one trusted
  project or org — enabling it for anyone enables it for every tenant
  sharing the process.
- `extra_functionality_code` is an ordinary `SolverConfig` field
  (`solver_service.py:125`, consumed at `:478`) set through
  `PUT /api/simulation/config`, which does a permissive
  `merged.update(submitted)` (`routers/simulation.py:347-350`).
- `POST /api/simulation/run` (`routers/simulation.py:586`) has **no admin
  gate**. `routers/simulation.py` contains no `Depends` and no
  `_require_admin_actor` — those exist only in `routers/admin.py`. The
  route is authenticated (the global gate) but not privileged.

Net: with the flag on, *any authenticated member* — not an operator, not an
admin — gets arbitrary in-process Python with full filesystem and network
access. That is a member→RCE escalation, gated only by a variable the
member cannot see but also cannot be excluded from. `GET
/api/simulation/capabilities` correctly surfaces the flag to the UI, so the
frontend story is fine; the authorization story is not. Minimum fix:
correct the docstring, and gate the field on org-admin rather than on a
process-wide flag.

### 3. Names arriving at the edge are not validated

Component names flow from request bodies into the network, into filenames,
and into model-facing text without a character-class check. Gap 1 is one
symptom (a delimiter in a bus name). The upload path shows the right
pattern — `_FILE_ID_RE` is anchored and narrow — and it is not applied to
names generally. This is the shared root cause behind gap 1 and worth
fixing as such, not just at the fence.

### 4. A refused `/stream` request still switches the session model

`routers/chat.py:564` writes `session.model` before
`chat_service.py:2746`'s `_turn_in_flight` guard refuses the request. A
request that is rejected therefore still mutates persistent state, so a
caller who cannot get a turn can still change which model the *next* turn
uses. Low severity, clean fix, already written up separately
(`docs/superpowers/findings/2026-09-10-a-refused-stream-request-still-switches-the-session-model.md`,
two options, neither applied).

### 5. Cookie policy is hardcoded to a preview vendor's domain

`routers/auth.py::_cookie_flags`:

```python
if hostname.endswith(".cursorusercontent.com"):
    return "none", True
```

`SameSite=None` is a real CSRF-surface widening, and here it is granted by
a hostname suffix compiled into the product. It is defensible for a preview
environment and indefensible as a permanent rule: the deployment decides
its own cookie policy, so this belongs in configuration, not in a literal.
The CSRF double-submit check (gap-list note: `main.py:645`) is what
currently carries the load for those sessions.

### 6. Verification gaps that make the above harder to trust

- The `Gridspine` CI job is **path-filtered** and skips entirely on changes
  that do not touch its paths. A green check mark on a PR therefore does
  not mean those tests ran. I got this wrong myself on PR #16 and had to
  correct it, which is the point: the signal reads as coverage and isn't.
- `dev-env` has been red since `14eae4d`.
- The local baseline carries 124 pre-existing failures, all
  `No module named 'gridspine'`. Any "the suite is green" claim about this
  backend is false; the only sound gate is *the failing set is unchanged*,
  which is what the refactor work used.

## Recommended next assessment

**Per-route authorization.** Gaps 2 and 4 are both instances of the same
unexamined question: the global gate establishes *that* a caller is
authenticated, and individual routes then decide — or fail to decide — what
that caller may do. `routers/simulation.py` having zero `Depends` while
`routers/admin.py` carries all the privilege checks is the shape of a
surface that grew a tenancy model after its routes were written. A
route-by-route inventory of "who may call this, and what enforces it" is
the highest-value thing to do next, and it subsumes gap 2 rather than
sitting beside it.
