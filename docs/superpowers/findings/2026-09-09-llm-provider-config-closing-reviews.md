# The two closing reviews the LLM provider config plan never got

**Ran:** 2026-09-09, against `claude/llm-provider-config-handover-m6adr7`.
**Why:** the handover
(`docs/superpowers/handovers/2026-09-01-llm-provider-config-handover.md`)
records both reviews as dispatched and **killed by an API session limit**,
producing no verdict, and says plainly: do not merge on green gates alone,
because this plan's reviews found defects in green code more than a dozen
times.

Two reviews, as that document scoped them:

1. **Whole-branch coherence** — cross-task coherence, dead code, surviving
   false-fact comments.
2. **Adversarial security** — scoped by the fact that an earlier review
   cleared the provider seam *on the grounds that `llm_openai_compat` had no
   production caller*. This branch is exactly what makes that verdict expire.

## Verdict

**Three findings, all fixed in the same commit as this record.** None is a
privilege boundary; the security controls this branch adds held under every
attack run against them. The one that matters is a destructive confirmation
that told the operator the wrong blast radius.

Method note, because it changes how much the "repelled" column is worth: the
attacks below were **executed**, not reasoned about. A pip venv built from
`gui-requirements.txt` ran the real routes, the real provider and the real
profile store. Where a claim is marked verified, a test was run that fails if
the control is removed.

---

## Findings

### 1. The key-removal confirmation stated a blast radius that is false — MODERATE

`DELETE /settings/llm/profiles/{id}/key` clears an environment *variable*.
There is only one copy. For a profile on a cataloged provider preset that
variable is the provider-wide key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …),
shared with every other profile on that provider — **including the two
built-ins, which are the zero-config default the whole instance falls back
to.**

The backend behaviour is correct and deliberate: `delete_llm_profile` scopes
its cleanup to the profile's *private* slot precisely to avoid disarming
others, and `DELETE .../key` stays aimed at the shared key because it is the
only route that can revoke one. Both halves are reasoned in place, and a test
pins each.

The **copy** was the defect:

> Remove the stored key for "Side Car"? *This model* will stop working until a
> new key is set.

Measured (`test_a1`, since promoted into `test_llm_settings_api.py`): create a
side profile on `preset="anthropic"`, remove its key, and `anthropic-sonnet`
and `anthropic-opus` both report `key_present: false`. One model named, every
Anthropic model affected — in the one dialog whose entire job is telling an
operator what they are about to break.

**Fixed** by making the sharing visible rather than by changing the
behaviour. `_profile_out` now returns the derived `key_env` (a *name*, never a
value; these names already ship to the client inside `presets.json`), and the
confirmation names every profile that loses the key with it.

Checked, not assumed: `_profile_out` feeds only the two super-admin-gated
routes. The member-visible `GET /chat/profiles` builds its own
`{id, label, wire}` dict and is untouched, so nothing new reaches an ordinary
member.

Deliberately server-derived rather than re-computed in TypeScript: the
built-ins are a special case — `preset="anthropic-sonnet"` is not a catalogue
id, yet they resolve to the `anthropic` entry's `key_env` — so a client-side
rule would have been wrong for exactly the two profiles whose silent loss
matters most.

### 2. `max_output_tokens` was type-checked but never range-checked — LOW

S-L3/C-22 contained what JSON could put in a profile's fields and stopped at
types. `chat_service` reads the field as
`profile.max_output_tokens or MAX_OUTPUT_TOKENS_PER_TURN`, so the two ends of
the range fail in opposite and equally quiet ways:

* **negative** is truthy — it reaches the wire as `max_tokens=-1` and every
  turn on that profile dies as an opaque upstream `invalid_request`, while
  Settings shows a budget that looks deliberate;
* **zero** is falsy — swallowed by the `or`, silently meaning "the default",
  so Settings displays a limit that is not in effect.

Neither needed a hand-edited file: `PUT /settings/llm/profiles/{id}` accepted
both (measured, 200 OK).

**Fixed** in `_validate_field_types` — beside the type checks it belongs
with, and reached from `_validate_profile`, so it fires on load and on save
alike. Same rule as those checks, and for the same reason: a save-only check
lets a downgrade or a hand edit put the value back. A stored 0 or negative now
becomes a skipped entry with a warning instead of a silent surprise, and C-14
keeps its bytes on disk to repair.

### 3. A stale rationale in `llm_openai_compat`'s docstring — LOW

> httpx is imported function-locally: it is a dev dependency **until plan 2
> adds it to gui-requirements.txt** …

Plan 2 added it, on this branch: `gui-requirements.txt:101` pins
`httpx==0.28.1` and states the real rule — the import must stay **unguarded**,
because a missing httpx has to fail the build rather than leave every
OpenAI-compatible provider silently dead in the shipped app. A reader
correcting the stale half could easily "fix" the wrong thing. Rewritten to
say what is true now and why the placement stays.

This is the branch's own recurring failure mode: Task 14's review caught a
disproven backend claim that had been written into a source comment as
verified fact.

---

## Attacks run, and repelled

Every row was executed against the real code. The trailing note says what
each proves that reading could not.

| Attack | Result |
|---|---|
| Cross-host redirect carrying the `Authorization` header, on `stream`, `probe` and `probe_models` | **Repelled.** `follow_redirects` defaults to False on the pinned httpx 0.28.1 and nothing overrides it; the attacker host is never contacted. The handover asserted this from the default — now measured on the version that ships. |
| `base_url` retargeting via fragment (`…/v1#@evil.test`), query, and dot segments | **Repelled.** Every request lands on the validated host. |
| Preset spoofing: `Anthropic`, `ANTHROPIC`, leading/trailing space, NUL, dotless `ı`, circled `ⓞ`, Cyrillic `о` | **Repelled**, and structurally so: the catalogue is an exact dict lookup, and every miss falls through to the profile's *private* `PYPSA_GUI_LLM_KEY__<SLUG>` slot. A near-miss preset cannot inherit a shared credential — it can only fail to inherit one. This was the handover's "not yet attacked" item. |
| A hostile endpoint echoing the caller's own key back in an error body | **Repelled.** Absent from both the `ProviderError` message and the log. |
| A hostile endpoint echoing the key back as model output | **Repelled.** Absent from the persisted transcript. |
| A hostile endpoint returning a `tool_use` for a tool that was never offered | **Repelled.** `tool_not_offered`, refused before the confirmation card and before the dispatcher lookup. |
| The same endpoint answering every request that way, forever | **Bounded.** 26 provider calls, then `tool_call_cap_exceeded` → `session_done`. F1's reasoning — that a refusal must *count against* the budget — holds under execution; the earlier `continue`-before-increment really would have spun. |
| C-13 orphan: a profile edited off its private slot to `auth="none"` | **Repelled.** The slot is cleared; no credential survives that no route can reach. |
| `set_active_profile` driven by an ordinary member | **Repelled.** Super-admin checked in the tool, matching the HTTP route, with confirmation-gating explicitly rejected as a substitute. |

Already covered by the branch's own suite and re-run, not re-litigated here:
the super-admin gate on all seven `/settings/llm*` routes (org admin → 403,
anonymous → 401), `ProfileIn`'s `extra="forbid"`, the preset↔`base_url` lock
on load as well as save, and the no-split merge precondition with its
discrimination test.

## Coherence pass

* **Dead code:** none. Every top-level definition in `llm_config`,
  `app_secrets`, `redaction`, `llm_anthropic` and `llm_openai_compat` has a
  live reference outside its own module.
* **New error kinds route to copy:** `not_authorized`, `tool_not_offered`,
  `tool_call_cap_exceeded` and `unknown_profile_id` all have `KIND_COPY`
  entries *and* `TOOL_ERROR_BANNER_KINDS` membership.
* **The carried `TOOL_ERROR_BANNER_KINDS` follow-up** (a kind that should
  route and doesn't — the direction the subset test cannot catch) was checked
  by hand for this branch's surface. `project_switched_mid_turn` is emitted on
  a `tool_error` frame and is *not* in the set, but the same guard also emits
  it as an `error` frame, which reaches the banner — so there is no
  user-visible gap here. **The follow-up itself stands:** this was checked by
  reading, which is exactly the manual step the shared manifest would
  replace.
* **False-fact comments:** one found (finding 3). Two load-bearing claims
  spot-checked and TRUE: `_validate_preset_base_url_lock`'s "no catalogue
  entry has `auth != bearer` with a non-null `key_env`" (holds for all six
  presets), and `_profile_out`'s "the hint format and the never-the-value
  guarantee are identical across both surfaces" (both go through
  `app_secrets.status`).

## What these reviews do NOT cover

* **The deferred-item triage the handover asks for.** Its ledger,
  `.superpowers/sdd/2026-08-14-llm-provider-config-and-switching/progress.md`,
  is git-ignored and exists only on the machine that ran the plan. It is not
  in this checkout, so the deferred minors could not be re-adjudicated. That
  half of the whole-branch review is still open.
* **The frontend beyond the LLM surface.** The security pass was scoped to
  the credential and provider path, as the handover scoped it.
* **`pixi run gui-tests`.** See the verification note below.

## Verification

Backend, in a pip venv built from `gui-requirements.txt` (pixi is not
installed in this container — the same caveat the 2026-09-09 probe run
carries, and for the same reason):

* `test_llm_settings_api.py` + `test_llm_config.py` — 145 passed
* `test_llm_provider_seam.py` + `test_no_split_merge_precondition.py` +
  `test_chat_api_key_settings.py` + `test_llm_config.py`, live anthropic probe
  enabled — 153 passed, 2 skipped (the openai/local-endpoint probes)
* full backend suite, live probe enabled — **14 failures, every one of them
  pre-existing and environmental.** Proven rather than asserted: the same 14
  were re-run against the unmodified tree (`08d950f0`) and failed identically.
  They are `pywebview` absent (the desktop/shutdown/packaging tests, 12),
  `libmagic` absent so MIME sniffing is skipped (`test_chat_uploads`, 1), and
  `psycopg` absent (`test_sqlite_pragmas`, 1) — all optional dependencies this
  venv deliberately or necessarily omits. None is in the LLM surface, and none
  is in a file this change touches.

Frontend: `npx tsc --noEmit` clean; `AssistantModelSettings.test.tsx` and
`LocalSettings.test.tsx` — 34 passed.

**The canonical `pixi run gui-tests` gate then ran, and is GREEN.** In the
`test` environment built from this repo's `pixi.lock` with `--locked`, with
the live anthropic probe enabled:

    3,252 passed, 24 skipped, 0 failed, 0 errors  (3,276 collected)

So the three fixes above are verified where it counts, not only in the pip
venv. That venv's 14 failures are absent here, as diagnosed — `pywebview`,
`libmagic` and `psycopg` are all in the pinned set — and
`test_desktop_downloads.py`'s two tests RAN rather than skipped, which is the
reason `pixi.toml` puts the task under `[feature.test.tasks]` at all. The
environment is on Python 3.12.13 where the venv was on 3.11, so that venv was
never this gate whatever it had installed.

The one skip worth naming is the openai-wire live probe, which needs a local
endpoint this container has not got; it passed against Ollama on 2026-09-04.
