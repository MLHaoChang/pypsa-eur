# Local Settings — design

**Date:** 2026-08-03
**Status:** approved, ready for planning
**Scope:** one Settings pane in the desktop app holding the Anthropic API key and a route to the log file.

## Problem

The packaged macOS app ships a complete chat feature that cannot run.

`ANTHROPIC_API_KEY` is read from `os.environ` at four sites — `main.py:780`
(the startup presence probe), `routers/chat.py:89` (the `anthropic_api_key_present`
flag the panel renders from), and `services/chat_service.py:1454` and `:1474` —
and the SDK reads it from the environment itself, which
`chat_service.py:1481` records as deliberate: *"SDK reads ANTHROPIC_API_KEY
from env by default — we DO NOT pass it explicitly as a kwarg so the literal
value can't accidentally show up in `__repr__` / logs."*

`backend/.env` supplies that variable in development. It is deliberately
excluded from the bundle: `smoke/check_bundle.py` enforces
`FORBIDDEN_PREFIXES = (".env",)` because that file carries a real key and the
`SECRET_KEY` that signs sessions. A `.app` launched from Finder sources no
shell profile, so in the packaged app the variable is unset by construction and
there is no surface anywhere in the UI to supply one.

`backend/tests/test_packaging_requirements.py:70` already records the
consequence in writing: pinning the `anthropic` package made the import work
and changed nothing else.

The second, smaller gap: `desktop/bootstrap.py:57`'s `install_file_logging()`
writes a rotating log to `app_data_dir()/pypsa-gui.log`, but nothing in the
application tells the user it exists. A packaged-app 500 produces a toast and
no discoverable trail.

### Explicitly out of scope

**Code signing and notarisation.** `build-macos.sh:115-134` already implements
the full sequence — `codesign --verify --deep --strict`, `notarytool submit
--wait`, `stapler staple`, and DMG signing at `:224-231` — gated on
`CODESIGN_IDENTITY` and `NOTARY_PROFILE`. Closing that gap requires an Apple
Developer Program membership and two environment variables, not code.

## Constraints inherited from the project

1. **One build serves both the desktop app and the web deployment.** No feature
   may exist only because the other mode was forgotten.
2. **No changes to core functionality.** The chat implementation is not
   touched by this work.
3. **Cross-platform.** The app is developed on Windows and macOS arm64;
   nothing may be macOS-only without a defined behaviour elsewhere.
4. **App-data problems must never prevent launch.** `install_file_logging`
   already establishes this rule and this work follows it.

## Architecture

Four units, each independently testable:

| Unit | File | Responsibility |
|---|---|---|
| Store | `backend/local_settings.py` (new) | read/write the JSON file; nothing else |
| Routes | `backend/routers/local_settings.py` (new) | HTTP surface, local-mode gated |
| Startup hook | `backend/main.py` (modify) | apply stored key to `os.environ` |
| Pane | `frontend/src/pages/LocalSettings.tsx` (new) | the UI |

### Naming

The store lives at the backend root beside `app_paths.py`, not under
`services/`, because it shares that module's property: it imports only stdlib
and `app_paths`, so it cannot participate in an import cycle.

The store module and the router module share the basename `local_settings` in
different packages. `main.py` needs both, so it must disambiguate explicitly:

```python
import local_settings as local_settings_store        # the store
from routers import local_settings                    # the router (added to
                                                      # the existing tuple at
                                                      # main.py:40)
```

The on-disk file is named `local-settings.json` — not `settings.json` — so it
is never confused with `backend/settings.py`'s pydantic `Settings`.

## The store

`backend/local_settings.py` owns `app_data_dir()/local-settings.json`.

```json
{ "anthropic_api_key": "sk-ant-..." }
```

**Interface:**

```python
def settings_path() -> Path                      # app_data_dir()/local-settings.json
def read_settings() -> dict[str, str]            # {} on any failure
def stored_api_key() -> str | None               # None when absent or blank
def write_api_key(key: str) -> None              # empty string removes the entry
```

**Required properties:**

- **Mode `0o600` at creation.** The file is opened with
  `os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)`. A `chmod`
  after writing is not acceptable: it leaves a window in which the key is
  world-readable.
- **Atomic replacement.** Write to a temporary file in the same directory,
  then `os.replace`. A crash mid-write must not leave a truncated file that
  reads as "no key".
- **Never fatal.** A missing directory is created. A missing, unreadable, or
  malformed file yields `{}` and is logged at WARNING. This mirrors
  `install_file_logging`'s rule that an app-data problem must not be the
  reason the app will not launch.
- **`write_api_key("")` removes the `anthropic_api_key` entry** rather than
  storing an empty string, so `stored_api_key()` has one representation of
  absence.

Windows note: `0o600` is honoured by CPython's `os.open` on Windows only
insofar as the platform supports it; the file lands under `LOCALAPPDATA`,
which is already per-user. The mode assertion in the test suite is therefore
POSIX-gated.

## The startup hook

In `main.py`, after the existing `load_dotenv` block and before any module
reads the variable:

```python
if not os.environ.get("ANTHROPIC_API_KEY"):
    _stored = local_settings_store.stored_api_key()
    if _stored:
        os.environ["ANTHROPIC_API_KEY"] = _stored
```

**The stored key never overrides an environment variable.** This mirrors the
`override=False` decision documented at `main.py:23` for `.env`, and it is what
keeps the web deployment and a developer shell with the key exported
completely unaffected. The file is a fallback, never an override.

## The routes

`backend/routers/local_settings.py`, mounted at prefix `/api/local-settings`.
**Every route carries `Depends(local_mode.reject_unless_local_mode)`.**

That guard already exists at `local_mode.py:78` and its docstring describes
precisely this case: *"In the desktop app the server and the user are the same
person... So the gate is not 'admin only', it is 'this deployment has exactly
one tenant, and they own the disk.'"* It returns 404, not 403 — the surface
does not exist in web mode.

### `GET /api/local-settings`

```json
{
  "key_set": true,
  "key_hint": "…7f3a",
  "log_path": "/Users/you/Library/Application Support/PyPSA Studio/pypsa-gui.log"
}
```

`key_hint` is the last four characters of the stored key, or `null` when no key
is stored **or when the stored key is shorter than eight characters** — for a
short or malformed value, "the last four characters" would disclose most of it.
**The key itself is never returned by any route.**

### `PUT /api/local-settings/anthropic-key`

Body: `{"api_key": "sk-ant-..."}`. An empty string clears the key.

Order of operations:

1. `write_api_key(key)` — persist first, so a probe failure cannot lose input
   the user just typed.
2. Set or delete `os.environ["ANTHROPIC_API_KEY"]`.
3. Probe (below), unless the key was cleared.

Response: `{"status": "...", "detail": "...", "key_set": bool, "key_hint": ...}`.

Setting `os.environ` takes effect immediately: `chat_service._build_anthropic_client()`
constructs a **fresh** client on every call and re-reads the environment each
time, so no restart is required to enable or disable chat.

### `POST /api/local-settings/reveal-log`

Reveals the log file in the platform file manager. Returns
`{"revealed": true}` or `{"revealed": false, "detail": "..."}`.

## The probe

```python
client = anthropic.Anthropic()   # reads the env var we just set
client.models.list(limit=1)
```

Verified against the pinned `anthropic==0.117.0`: `models.list` exists and
accepts `limit`. It lists model metadata and bills no tokens.

| Outcome | `status` |
|---|---|
| returns | `valid` |
| `anthropic.AuthenticationError`, `anthropic.PermissionDeniedError` | `rejected` |
| `anthropic.APIConnectionError`, any other exception | `unreachable` |
| `ImportError` on `import anthropic` | `sdk_not_installed` |

All four exception classes were verified present in 0.117.0.
`sdk_not_installed` should be unreachable — the package is pinned in
`gui-requirements.txt` — but `_build_anthropic_client` already models that
state and this surface matches it rather than inventing a different vocabulary.

**The key is saved regardless of the outcome.** Being offline is not a reason
to refuse to store a key. But `unreachable` is reported as `unreachable` and
never as success: a stored key whose validity was never confirmed must not
render as a confirmed one. This is the same unset-versus-zero rule the
2026-08-01 trustworthy-numbers work enforced on economic surfaces, applied to
credentials.

Exception messages are passed through `chat_service._redact_for_log` before
being logged or returned in `detail`.

## Reveal

This introduces the **first `subprocess` invocation anywhere in the
application**. It is acceptable here for one specific reason and the design
depends on it:

**No part of the command comes from the request.** The path is computed
server-side as `app_paths.app_data_dir() / "pypsa-gui.log"`. There is no
user-supplied argument to inject into, because there is no user-supplied
argument at all. The route body takes no parameters.

Containment:

- `subprocess.run([...], shell=False, check=False, timeout=10)`, list argv.
- Gated by `reject_unless_local_mode` like the rest of the router.
- Platform dispatch:
  - darwin: `["open", "-R", str(path)]`
  - win32: `["explorer", f"/select,{path}"]`
  - otherwise: `["xdg-open", str(path.parent)]` — Linux has no portable
    reveal-and-select, so the containing directory is opened instead.
- Any failure returns `{"revealed": false, "detail": ...}` with HTTP 200. The
  pane still displays the path and a Copy button, so the feature degrades to
  the no-subprocess design rather than dead-ending.
- The log file is created if absent before revealing, so a first-run reveal
  does not open a Finder window selecting nothing.

## The pane

`frontend/src/pages/LocalSettings.tsx`, wired in three places:

1. `frontend/src/store/uiStore.ts:30` — add `'settings'` to the `SlidePanel`
   union.
2. `frontend/src/App.tsx` — add `settings: { eyebrow: 'APPLICATION', title:
   'Settings' }` to `PANEL_META`, and `case 'settings': return
   <LocalSettings />` to `fullPageContent`.
3. `frontend/src/layout/Sidebar.tsx` — a new `SItem` row following the exact
   shape of the `Solver Settings` row at `Sidebar.tsx:1282-1286`. `Settings2`
   is already taken by that row, so add `SlidersHorizontal` to the existing
   `lucide-react` import (verified present in the installed package):

   ```tsx
   <SItem icon={<SlidersHorizontal size={15} />} label="Settings"
     title="Store your Anthropic API key and find the application log."
     active={activeSlidePanel === 'settings'}
     onClick={() => { setSlidePanel(activeSlidePanel === 'settings' ? null : 'settings'); onCloseModal?.() }}
   />
   ```

4. `frontend/src/components/CommandPalette.tsx` — a command entry beside the
   existing `simparams` one at `CommandPalette.tsx:346`, so the pane is
   reachable the same two ways every other panel is.

`PANEL_META` is typed `Record<SlidePanel, ...>`, so TypeScript enforces that
step 2 happens once step 1 does.

The pane is **not** added to `FULL_SCREEN_TABS` (`App.tsx:118`); it opens as a
half-width panel beside the canvas, like `Solver Settings`.

Contents:

- **Anthropic API key.** A masked input, **never populated with the stored
  key**. When a key exists the placeholder reads `Key set — ending 7f3a`. Save
  is disabled while the field is empty, so an accidental Save can never wipe a
  stored key; clearing is a separate explicit `Clear` action that sends the
  empty string and asks for confirmation first.
- **Status line**, rendering the four probe outcomes distinctly — `valid`,
  `rejected`, `unreachable`, `sdk_not_installed` — never collapsing the last
  three into one "failed".
- **Diagnostics.** The log path as selectable text, a `Reveal in Finder`
  button, and a `Copy path` button.

**The pane hides entirely when `GET /api/local-settings` returns 404**, which
is what web mode returns. The nav entry is hidden in the same condition. This
is what lets one build serve both modes.

Pure mapping functions — probe status to display text, `GET` payload to pane
state — are exported for direct unit testing, following the pattern
established for `Economics.tsx` in the trustworthy-numbers work.

## Testing

### Store

- Round trip: write then read returns the key.
- `write_api_key("")` removes the entry; `stored_api_key()` returns `None`.
- Written file has mode `0o600` (POSIX-gated).
- Malformed JSON yields `{}` and logs at WARNING; does not raise.
- Missing app-data directory is created.
- Atomicity: a temporary file left in the directory does not shadow the real
  read.

### Startup precedence

- `ANTHROPIC_API_KEY` set in the environment + a different key on disk → the
  environment value survives untouched.
- Environment unset + key on disk → the stored key is applied.
- Environment unset + no file → no key, no exception.

### Routes

- **All three routes return 404 when `PYPSAGUI_LOCAL_MODE` is unset.** This is
  the security property of this design and must be an executable assertion,
  not a comment.
- `GET` never includes the key literal in its response body.
- `PUT` with an empty string clears both the file entry and `os.environ`.
- `PUT` persists the key even when the probe reports `unreachable`.

### Probe mapping

Each exception class maps to its documented status, driven by a fake client
that raises on `models.list`. Covers all four outcomes.

### Secret hygiene

After a full save-and-probe cycle, the key literal appears in neither the `GET`
response body nor `pypsa-gui.log`. `_redact_for_log` already covers formatted
messages; this asserts the new code path independently.

### Reveal

`subprocess.run` is patched; assert the argv per platform and that no element
of it derives from request input. Assert a raising `subprocess.run` produces
`{"revealed": false}` with HTTP 200 rather than a 500.

### Packaging

`smoke/check_bundle.py` — add `local-settings.json` to `FORBIDDEN_FILES`
(`check_bundle.py:38`). The existing rules cover `.env*` prefixes, `.db`
suffixes and `pypsa-gui.db` by name; a settings file containing a live API key
belongs on that list for the same reason and would otherwise ship if a build
ever ran from an app-data directory.

### Frontend

Mapping-function unit tests for the four probe statuses and for the
`key_set`/`key_hint` rendering, plus the hidden-on-404 case. No end-to-end
browser test.

## What this does not do

- Does not store any credential other than the Anthropic key.
- Does not make projects, database or app-data paths configurable — they are
  not even displayed. `app_paths.py` resolves them and that remains the single
  source.
- Does not add an in-app log viewer. `Reveal in Finder` plus the path is the
  whole diagnostics surface; a viewer would need its own redaction pass over
  file contents, which is a larger piece of work than the gap justifies.
- Does not touch chat. The four existing `os.environ` read sites and the SDK
  are unchanged; the entire integration is that the variable is now set.
