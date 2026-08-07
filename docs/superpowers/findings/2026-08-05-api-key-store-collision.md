# Two API-key stores, one environment variable — the merge stacked them

**Date:** 2026-08-05
**Found by:** a pre-merge audit of `master` → `feature/local-app-impl`, before
`451f775a`. Predicted from the diff, then confirmed against the merged tree.
**Status:** FIXED. Was live on `feature/local-app-impl`, and on `master` as of
the fast-forward to `96a769f3`.
**Heads-up for:** whoever owns U-1 / local-settings. Two sessions built the
same feature on two branches; this is the seam, not anybody's mistake.

> **Resolution.** `local_settings` now delegates storage to `app_secrets`;
> `local-settings.json` survives only as a migration source, and `main.py` runs
> the migration instead of the dead second publisher. Both Settings surfaces
> and both endpoints are unchanged from the user's point of view. See the
> "Suggested direction" section at the bottom — that is what was built, with
> one correction found while building it, recorded there.

## What happened

`master` and `feature/local-app-impl` each grew an answer to the same problem —
the packaged app ships no `.env`, so it has no way to receive an
`ANTHROPIC_API_KEY`. Neither knew about the other.

| | master (U-1) | feature |
|---|---|---|
| store | `<app-data>/user.env`, `KEY=VALUE` | `<app-data>/local-settings.json` |
| module | `services/app_secrets.py` | `local_settings.py` |
| route | — | `PUT /api/local-settings/anthropic-key` |
| UI | `components/ApiKeySetup.tsx` | `pages/LocalSettings.tsx` |

Only three files overlap between the two branches, and `main.py` is one of
them. **Git reported no conflict** — `git merge-tree` returns a clean tree —
because master rewrote the top of `main.py` while the feature branch appended
below it. Both edits applied. Both mechanisms now run, in this order:

```python
app_secrets.bootstrap_environment(backend_env=Path(__file__).parent / ".env")   # master
...
local_settings_store.apply_to_environ()                                          # feature
```

## Why the second one is dead

`apply_to_environ` opens with a guard that the first call has already
falsified:

```python
if os.environ.get("ANTHROPIC_API_KEY"):
    return False
```

`bootstrap_environment` set that variable one statement earlier. So whenever
`user.env` holds a key, **the key saved through the Settings pane is silently
discarded** — no exception, no log line, no UI signal.

Measured, with a different key deliberately placed in each store:

```
user.env            -> sk-ant-FROM-user-env-AAAA
local-settings.json -> sk-ant-FROM-local-settings-json-BBBB

bootstrap_environment() -> sk-ant-FROM-user-env-AAAA
apply_to_environ()      -> returned False
live ANTHROPIC_API_KEY  -> sk-ant-FROM-user-env-AAAA

ApiKeySetup.tsx   (app_secrets.status): configured=True source=settings hint=…AAAA
LocalSettings.tsx (local_settings):     key_set=True                    hint=BBBB
```

The last two lines are the user-visible harm. **The Settings pane advertises a
key that is not the one in use** — it reads its own store, which nothing
consumes. A user who pastes a working key there, sees it saved with the right
last-four, and still gets `missing_api_key` from chat has no way to find out
why.

It is also order-dependent, which makes it worse to diagnose than a flat
breakage: with only `local-settings.json` populated, `apply_to_environ` does
fire and everything works. The failure needs both stores populated, so it
appears only after a user has touched both panes — or after saving in one pane
on a machine where the other was used first.

## Collateral

`local_settings.apply_to_environ`'s docstring cites "`load_dotenv(override=False)`
at `main.py:23`" and explains itself as a deliberate contrast to it. Master
deleted that loader; the line it names is now the `app_secrets` import. Twenty
lines of correct reasoning about code that is no longer there.

## The direction taken

Keep both surfaces, unify the storage: `local_settings` reads and writes
through `app_secrets` instead of owning `local-settings.json`. Both UIs and
both endpoints keep working, and one module keeps owning precedence.

`app_secrets` is the better host for that responsibility — it has the
`MANAGED_KEYS` allowlist (so this file can never be used to set `SECRET_KEY` or
repoint `PYPSAGUI_APP_DATA_DIR`), it tracks which names the launching shell
supplied so a shell value is never clobbered, and `status()` already reports
`source` and `overridden_by_environment`, which is exactly what a Settings
pane needs in order to explain why a save appeared to do nothing.

Two things any fix must not skip:

- **Migrate existing keys.** A user with a key in `local-settings.json` and
  nothing in `user.env` is working today. Dropping that file without carrying
  the value over breaks them at the next launch.
- **Remove the second call from `main.py`,** rather than leaving a no-op. A
  dead call that looks load-bearing is how this arrangement survived review in
  the first place.

## The correction, found while building it

The migration was written to run BEFORE `bootstrap_environment` — reasoning
that a migrated key then gets published in the same startup rather than the
next one. That is wrong, and the way it is wrong is silent.

`set_secret` refuses to overwrite a variable the launching shell supplied, but
it recognises one only through `app_secrets._SHELL_NAMES` — which
`bootstrap_environment` is what populates. Migrating first runs that guard
against an empty set, so it clobbers a key the operator exported on the command
line. Caught by a hand-written check of the three startup shapes before any
test existed; the ordering is now the other way round, and nothing is lost by
it because `set_secret` publishes to `os.environ` itself.

`smoke/repro_api_key_collision.py` is the check, kept and inverted: it runs all
three shapes against throwaway app-data and reports which store won, what each
pane would show, and whether they cohere. Useful the next time someone reports
"I saved my key and chat still says it is missing".

## One thing to know if you write tests near this

`import pypsa` loads `backend/.env` into `os.environ`. `tests/conftest.py`
imports pypsa before it imports `main`, so by the time `bootstrap_environment`
runs in the test process, `_SHELL_NAMES` already contains every name in a
developer's `.env` — and every saved key looks masked by the shell.

`main.py` is not exposed to this: it calls `bootstrap_environment` above its
own third-party imports for exactly this reason, and a packaged app ships no
`.env`. But an in-process test that does not reset `_SHELL_NAMES` is measuring
the developer's checkout rather than the app. `test_local_settings_startup.py`
sidesteps it entirely by running in a subprocess, which is the only honest way
to observe a module-level call.
