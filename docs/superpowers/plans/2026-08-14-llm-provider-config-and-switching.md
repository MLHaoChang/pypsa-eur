# LLM Provider Config and Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users run the assistant on Anthropic, OpenAI, Moonshot, Qwen, Ollama, LM Studio or any OpenAI-compatible endpoint, switched via profiles in a settings pane and a chat dropdown, with capability-honest degradation and UI-led guidance.

**Architecture:** A config layer below the (already-merged) provider seam: `llm_config.py` profile store + bundled preset catalogue + widened `app_secrets` allowlist. The turn path binds sessions to resolved profiles; providers are constructed from profiles. Frontend: profile dropdown, `AssistantModelSettings` section, generalized banner, KIND_COPY error rendering.

**Tech Stack:** FastAPI backend (pixi, pytest), httpx (pinned), React+TS frontend (vitest), PyInstaller bundle.

**Specs:** `docs/superpowers/specs/2026-08-13-llm-provider-config-and-switching-design.md` (authoritative; its Decision ledger and Verified constraints bind this plan) building on `docs/superpowers/specs/2026-08-05-llm-provider-seam-design.md` (note its "As built" event-vocabulary block).

## Global Constraints

- **Gates:** backend `pixi run gui-tests` (repo root; NEVER bare pytest for the full suite — 7 webview fails = wrong env); single test `pixi run -e test python -m pytest pypsa-gui/backend/tests/<file>.py -v`; frontend `npm test` in `pypsa-gui/frontend` (vitest run).
- **Zero-config invariant:** with no `llm-profiles.json` and only `ANTHROPIC_API_KEY` set, behaviour is byte-identical to today. The suites pass with NO key and NO profiles file; any new requirement is a defect.
- **All line numbers are approximate — locate every edit site by symbol name** (ledger-recorded lesson from plan 1; a trunk merge of `feature/local-app-impl` is expected to land mid-plan and will move frontend anchors, especially `ChatPanel.tsx`. Re-run `git log --oneline -3` + re-locate before each frontend task).
- **Secrets:** no route response, log line, or persisted record ever carries a key or full base URL (host:port max). `SECRET_KEY`, `PYPSAGUI_APP_DATA_DIR`, `DATABASE_URL` stay unwritable through `app_secrets`. Env names are ALWAYS UPPERCASE (Windows upper-cases `os.environ` keys; mixed case breaks `_SHELL_NAMES` precedence).
- **No magic counts:** assert against `len(TOOLS)` / registry contents, never literals (the 117-vs-120 merge failure).
- **Model literals** for built-ins live in `services/llm_config.py` only — `test_chat_models.py::test_no_module_hardcodes_a_model_literal` statically scans `chat_service` + `chat_tools`.
- **Error-kind retryability:** only `rate_limited`/`upstream_error` retry. New kinds (`capability_unsupported`, `profile_switch_requires_new_chat`) are harness-level frame kinds, terminal, never added to `_RETRYABLE_SDK_KINDS`.
- **Preset catalogue data must be verified against live vendor docs** during Task 2 — never asserted from memory.
- **TDD every task** (RED/GREEN verbatim in report); comment/doc-only diffs name the exemption.
- Deferred items folded in from the seam ledger: M5 (validate `LLMEvent.type` against `EVENT_TYPES` — Task 6), M6 (`max_completion_tokens` fallback + id-less tool deltas — Task 2/Task 6), T6 (mixed text+tool_result turns — Task 6).
- **Frontend test-mock trap:** `ApiKeySetup.test.tsx` uses a full-factory `vi.mock('../api/chat', ...)` with no `importOriginal` — any new import in the component MUST be added to the mock in the same commit. The four ChatPanel suites use `importOriginal` spreads; new API calls there need explicit mocks or they hit real axios in jsdom.

## File Structure

| File | Responsibility |
|---|---|
| Create `backend/services/llm_config.py` | profile dataclass, store (load/validate/save 0600), built-in synthesis, preset catalogue loader, key-slot derivation, active resolution. Owns built-in model literals. Imports: stdlib, `app_paths`, `services.app_secrets` only |
| Create `backend/presets.json` | catalogue data (verified in Task 2) |
| Modify `backend/services/app_secrets.py` | `is_managed_key` rule, `_read/_write_managed` rewrite, `live_secret_values`, `MAX_VALUE_LENGTH` |
| Modify `backend/services/redaction.py` | value-substitution pass over `live_secret_values()` |
| Modify `backend/desktop/bootstrap.py` | httpx/httpcore logger caps |
| Modify `backend/routers/chat.py` | new `/chat/settings/llm/*` + `/chat/profiles` routes; health additions; profile resolution before the script branch |
| Modify `backend/services/chat_service.py` | session binding, profile-based provider construction, capability enforcement, A8 generalisation, prompt split, `profile_id` in turn records |
| Modify `backend/services/chat_tools.py` + `chat_tools_schema.py` | `set_active_profile` tool; vision sub-call through the profile |
| Modify `backend/smoke/check_bundle.py`, `pypsa-gui.spec`, `backend/tests/conftest.py` | forbidden files, `presets.json` datas, autouse profile-file cleanup |
| Modify `frontend/src/api/chat.ts`; create `frontend/src/api/llmSettings.ts` | types → `string`, profile fields, settings client |
| Modify `frontend/src/store/chatStore.ts`, `components/ChatPanel.tsx` | `profileId`, `startNewChat()`, dropdown, frame consumers, KIND_COPY |
| Create `frontend/src/components/AssistantModelSettings.tsx` | profile CRUD + key + Test connection |
| Modify `frontend/src/components/ApiKeySetup.tsx`, `pages/LocalSettings.tsx`, `layout/Sidebar.tsx`, `components/CommandPalette.tsx`, `store/uiStore.ts` | banner generalisation, section hosting + gating, `requestSettingsSection` |

---

### Task 1: `services/llm_config.py` — profile store

**Files:**
- Create: `pypsa-gui/backend/services/llm_config.py`
- Test: `pypsa-gui/backend/tests/test_llm_config.py` (new)

**Interfaces:**
- Produces (exact, everything later depends on these):

```python
DEFAULT_MODEL: str = "claude-sonnet-5"   # moves here from chat_service (Task 5 re-imports)
OPUS_MODEL: str = "claude-opus-5"
BUILTIN_SONNET_ID = "anthropic-sonnet"
BUILTIN_OPUS_ID = "anthropic-opus"

@dataclass(frozen=True)
class LLMProfile:
    id: str                       # slug [a-z0-9-]{1,48}
    label: str
    preset: str                   # catalogue id or "custom"
    wire: str                     # "anthropic" | "openai"
    base_url: str | None          # None = wire's default endpoint
    model: str                    # free text, non-empty
    tools: bool                   # capabilities flattened for frozen dataclass
    vision: bool
    auth: str                     # "bearer" | "none"
    fallback_model: str | None
    max_output_tokens: int | None
    @property
    def key_env(self) -> str | None: ...   # DERIVED — never stored, never client-supplied

class ProfileValidationError(ValueError): ...
def derive_key_env(profile_id: str, preset: str) -> str | None
def load_profiles() -> tuple[list[LLMProfile], str]   # (profiles, active_id); synthesizes built-ins when file absent/corrupt — NEVER raises
def save_profiles(profiles: list[LLMProfile], active_id: str) -> None  # 0600, validates first
def resolve_profile(profile_id: str | None) -> LLMProfile   # None/unknown → active; legacy model strings NOT handled here (Task 7)
def resolve_active() -> LLMProfile
def set_active(profile_id: str) -> None
def load_presets() -> list[dict]   # bundled presets.json; [] on absence (dev checkouts pre-Task 2)
def profiles_path() -> Path        # <app-data>/llm-profiles.json
```

- Validation rules (each a `ProfileValidationError`): bad slug; wire not in {"anthropic","openai"}; empty model; `base_url` with userinfo (`@` before host) or credential-shaped query (`key=`/`token=`/`api_key=` params) or non-http(s) scheme; `auth` not in {"bearer","none"}.
- `derive_key_env`: preset in catalogue → its declared `key_env` (or None for `auth: none`); `"custom"` → `"PYPSA_GUI_LLM_KEY__" + profile_id.upper().replace("-", "_")`.
- Built-ins: two Anthropic profiles sharing `ANTHROPIC_API_KEY`, wire `anthropic`, `base_url None`, `tools/vision True`, `fallback_model` = `DEFAULT_MODEL` on the opus one only, active = `BUILTIN_SONNET_ID`. Built-ins are ALWAYS present (file profiles append; a file profile may not reuse a built-in id).
- Store re-reads per call — no `lru_cache` (test isolation depends on it). Corrupt/unreadable/non-object file → log warning, built-ins only.

- [ ] **Step 1: failing tests** — write `test_llm_config.py`:

```python
"""Profile store (spec 2026-08-13). App-data is redirected per-test."""
from __future__ import annotations
import json
import pytest


@pytest.fixture()
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_zero_config_synthesizes_builtins_and_writes_nothing(appdata):
    from services import llm_config
    profiles, active = llm_config.load_profiles()
    ids = [p.id for p in profiles]
    assert ids[:2] == ["anthropic-sonnet", "anthropic-opus"]
    assert active == "anthropic-sonnet"
    sonnet = profiles[0]
    assert sonnet.wire == "anthropic" and sonnet.model == llm_config.DEFAULT_MODEL
    assert sonnet.key_env == "ANTHROPIC_API_KEY"
    assert profiles[1].fallback_model == llm_config.DEFAULT_MODEL
    assert not llm_config.profiles_path().exists()  # synthesis never writes


def test_corrupt_file_never_raises(appdata):
    from services import llm_config
    llm_config.profiles_path().write_text("{not json", encoding="utf-8")
    profiles, active = llm_config.load_profiles()
    assert [p.id for p in profiles] == ["anthropic-sonnet", "anthropic-opus"]


def test_save_and_reload_roundtrip_0600(appdata):
    import os, stat
    from services import llm_config
    p = llm_config.LLMProfile(
        id="ollama-local", label="Ollama (local)", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([p], "ollama-local")
    mode = stat.S_IMODE(os.stat(llm_config.profiles_path()).st_mode)
    assert mode == 0o600
    profiles, active = llm_config.load_profiles()
    assert active == "ollama-local"
    got = {q.id: q for q in profiles}
    assert got["ollama-local"].base_url == "http://localhost:11434/v1"
    assert "anthropic-sonnet" in got  # built-ins always present


def test_custom_key_env_is_derived_uppercase(appdata):
    from services import llm_config
    assert (llm_config.derive_key_env("ollama-local", "custom")
            == "PYPSA_GUI_LLM_KEY__OLLAMA_LOCAL")


@pytest.mark.parametrize("base_url", [
    "https://user:pass@evil.example/v1",         # userinfo
    "https://h.example/v1?api_key=sk-live",      # credential query
    "ftp://h.example/v1",                        # scheme
])
def test_base_url_validation_rejects_credentials(appdata, base_url):
    from services import llm_config
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config.save_profiles([llm_config.LLMProfile(
            id="x", label="x", preset="custom", wire="openai",
            base_url=base_url, model="m", tools=True, vision=False,
            auth="bearer", fallback_model=None, max_output_tokens=None)], "x")


def test_resolve_unknown_falls_back_to_active(appdata):
    from services import llm_config
    assert llm_config.resolve_profile("nope").id == "anthropic-sonnet"
    llm_config_profiles, _ = llm_config.load_profiles()
    llm_config.set_active("anthropic-opus")
    assert llm_config.resolve_active().id == "anthropic-opus"
```

- [ ] **Step 2:** run — expect FAIL `No module named 'services.llm_config'`.
- [ ] **Step 3:** implement per the Interfaces block. Body notes: `load_profiles` merges built-ins first, then file entries (validated one-by-one; an invalid entry logs + is skipped, never aborts the load); `save_profiles` validates all, refuses built-in-id collisions, writes JSON `{"version": 1, "active_profile_id": ..., "profiles": [...]}` via `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + re-chmod (the `app_secrets._write_managed` pattern); `set_active` rejects unknown ids with `ProfileValidationError`. Follow `local_settings.read_settings`'s never-raise discipline (FileNotFoundError/OSError/UnicodeDecodeError/ValueError branches).
- [ ] **Step 4:** run — expect PASS all.
- [ ] **Step 5:** commit `feat(chat): llm_config — profile store with built-in synthesis`.

---

### Task 2: Preset catalogue (research-verified) + packaging

**Files:**
- Create: `pypsa-gui/backend/presets.json`
- Modify: `pypsa-gui/pypsa-gui.spec` (the `datas` list — locate `datas=`), `pypsa-gui/backend/smoke/check_bundle.py` (`FORBIDDEN_FILES`)
- Test: `pypsa-gui/backend/tests/test_llm_config.py` (append), `tests/test_packaging_requirements.py` untouched (httpx already pinned)

**Interfaces:**
- Produces `presets.json` entries with EXACTLY these keys: `id, label, wire, base_url, auth, key_env, tools, vision, suggested_models (list), help (short user-facing string)`. Ids: `anthropic, openai, moonshot, dashscope, ollama, lmstudio`. (`custom` is a UI affordance, not a catalogue row.)
- `llm_config.load_presets()` (Task 1) reads it from beside the backend package (`Path(__file__).parent.parent / "presets.json"`), and in the frozen bundle from the datas entry.

- [ ] **Step 1 (RESEARCH — do this before writing any data):** verify against LIVE vendor documentation (WebSearch/WebFetch): Anthropic + OpenAI + Moonshot (Kimi, international endpoint) + DashScope (Qwen, international) base URLs and current chat-completions compatibility paths; Ollama (`http://localhost:11434/v1`) and LM Studio (`http://localhost:1234/v1`) defaults; 2-3 current suggested model ids per vendor (e.g. whatever the current Kimi K-series and Qwen flagship ids actually are — do not trust this plan's examples). Record each claim + source URL in the task report. `auth`: bearer for the four clouds (`OPENAI_API_KEY`, `MOONSHOT_API_KEY`, `DASHSCOPE_API_KEY`, `ANTHROPIC_API_KEY`), `none` + `key_env: null` for ollama/lmstudio. `wire`: `anthropic` for the anthropic preset, `openai` for all others. Also verify the M6 deferral: whether OpenAI's chat-completions now requires `max_completion_tokens` — record the answer for Task 6.
- [ ] **Step 2: failing tests** (append to `test_llm_config.py`):

```python
def test_presets_catalogue_shape():
    from services import llm_config
    presets = llm_config.load_presets()
    ids = {p["id"] for p in presets}
    assert ids == {"anthropic", "openai", "moonshot", "dashscope",
                   "ollama", "lmstudio"}
    for p in presets:
        assert set(p) == {"id", "label", "wire", "base_url", "auth",
                          "key_env", "tools", "vision", "suggested_models",
                          "help"}
        assert p["wire"] in ("anthropic", "openai")
        if p["auth"] == "none":
            assert p["key_env"] is None
        else:
            assert p["key_env"] == p["key_env"].upper()


def test_local_presets_are_keyless():
    from services import llm_config
    by_id = {p["id"]: p for p in llm_config.load_presets()}
    assert by_id["ollama"]["auth"] == "none"
    assert by_id["lmstudio"]["auth"] == "none"
```

- [ ] **Step 3:** run RED (`load_presets()` returns `[]` → set mismatch), write `presets.json` from the verified research, add the `pypsa-gui.spec` datas entry (`("backend/presets.json", "backend")` — match the existing tuple style in the file), append `"user.env"` and `"llm-profiles.json"` to `check_bundle.py`'s `FORBIDDEN_FILES` with comments in that file's style.
- [ ] **Step 4:** run GREEN; also `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_packaging_requirements.py -q`.
- [ ] **Step 5:** commit `feat(chat): verified provider preset catalogue + bundle rules`.

---

### Task 3: `app_secrets` widening

**Files:**
- Modify: `pypsa-gui/backend/services/app_secrets.py`
- Test: `pypsa-gui/backend/tests/test_app_secrets.py` (append new tests; existing tests must pass UNMODIFIED except none should need modification)

**Interfaces:**
- Produces: `is_managed_key(name: str) -> bool`; `KNOWN_PROVIDER_KEYS: tuple = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY")`; `live_secret_values() -> frozenset[str]`; `MAX_VALUE_LENGTH = 2000`. `MANAGED_KEYS` stays as an alias for `KNOWN_PROVIDER_KEYS` (grep callers first; `local_settings.py` and `routers/` reference specific names, not the tuple).
- Rule: `is_managed_key(n)` ⇔ `n in KNOWN_PROVIDER_KEYS` or (`n.startswith("PYPSA_GUI_LLM_KEY__")` and suffix matches `[A-Z0-9_]{1,64}`).

- [ ] **Step 1: failing tests** (append):

```python
def test_is_managed_key_rule():
    from services import app_secrets as s
    assert s.is_managed_key("OPENAI_API_KEY")
    assert s.is_managed_key("PYPSA_GUI_LLM_KEY__OLLAMA_LOCAL")
    assert not s.is_managed_key("SECRET_KEY")
    assert not s.is_managed_key("PYPSAGUI_APP_DATA_DIR")
    assert not s.is_managed_key("DATABASE_URL")
    assert not s.is_managed_key("PYPSA_GUI_LLM_KEY__bad-lower")
    assert not s.is_managed_key("PYPSA_GUI_LLM_KEY__")


def test_saving_key_a_does_not_erase_key_b(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    from services import app_secrets as s
    s.set_secret("PYPSA_GUI_LLM_KEY__A1", "value-aaaa-1234")
    s.set_secret("OPENAI_API_KEY", "value-bbbb-5678")
    assert s.get_stored("PYPSA_GUI_LLM_KEY__A1") == "value-aaaa-1234"
    assert s.get_stored("OPENAI_API_KEY") == "value-bbbb-5678"


def test_live_secret_values_covers_shell_only_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "shell-only-value-99")
    from services import app_secrets as s
    s.set_secret("PYPSA_GUI_LLM_KEY__F1", "file-value-1234")
    vals = s.live_secret_values()
    assert "shell-only-value-99" in vals      # env-only, never in user.env
    assert "file-value-1234" in vals
```

- [ ] **Step 2:** RED (no `is_managed_key`).
- [ ] **Step 3:** implement: `is_managed_key` per rule; replace every `name in MANAGED_KEYS` / `k in MANAGED_KEYS` membership site with `is_managed_key(...)` — locate ALL of them: `_read_managed` filter, the four guard sites in `get_stored`/`status`/`set_secret`/`clear_secret`; rewrite `_write_managed` body to `for name in sorted(values)` with `if is_managed_key(name) and values.get(name)` (write-side allowlist preserved as a filter); `live_secret_values()` exactly as the spec's snippet (env scan by rule ∪ file values, blanks excluded); `MAX_VALUE_LENGTH = 2000` (update its comment). Do NOT change `status()`'s response shape (exact-dict test pins it). Update the module docstring's ALLOWLIST paragraph.
- [ ] **Step 4:** GREEN + full file: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_app_secrets.py pypsa-gui/backend/tests/test_local_settings_store.py pypsa-gui/backend/tests/test_local_settings_api.py pypsa-gui/backend/tests/test_local_settings_startup.py -q` — all pass unmodified.
- [ ] **Step 5:** commit `feat(chat): widen app_secrets allowlist to a rule with per-profile slots`.

---

### Task 4: Redaction widening + logger caps + leak sites

**Files:**
- Modify: `pypsa-gui/backend/services/redaction.py`, `backend/desktop/bootstrap.py` (locate `install_file_logging`), `backend/services/chat_service.py` (the tool_error persist site — locate `str(detail or exc)[:1000]`), `backend/services/chat_tools.py` (locate `vision sub-call raised`)
- Test: `pypsa-gui/backend/tests/test_llm_provider_seam.py` (append a redaction section) or a new `test_redaction_widening.py`

**Interfaces:**
- `redaction.redact_secrets_in_str` and `redact_for_log` both additionally substitute every value from `app_secrets.live_secret_values()` that is ≥ 8 chars, snapshotted ONCE per top-level call (`_redact_for_persist` recursion passes the snapshot down — add an optional `_values` parameter threaded through, or snapshot in a small wrapper; do not read the file per string).

- [ ] **Step 1: failing tests:**

```python
def test_redaction_substitutes_managed_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PYPSA_GUI_LLM_KEY__X1", "supersecretvalue42")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")   # 6 chars — below floor
    from services import redaction
    out = redaction.redact_secrets_in_str(
        "err: supersecretvalue42 while ollama ran")
    assert "supersecretvalue42" not in out
    assert "ollama ran" in out   # short values are NOT substituted


def test_tool_error_content_is_redacted(tmp_path, monkeypatch):
    # the chat_service tool_error persist site wraps content in redaction
    monkeypatch.setenv("PYPSA_GUI_LLM_KEY__X2", "anotherlongsecret77")
    from services import chat_service
    import inspect
    src = inspect.getsource(chat_service)
    # the raw f-string/str(...) is gone from the collector append site
    assert "str(detail or exc)[:1000]" not in src


def test_bootstrap_caps_httpx_loggers():
    import logging
    from desktop import bootstrap  # noqa: F401 — importing applies nothing;
    # call the logging installer with a tmp dir? install_file_logging touches
    # app-data; instead assert the module sets the levels at install time by
    # inspecting source for the two getLogger calls.
    import inspect
    src = inspect.getsource(bootstrap)
    assert 'getLogger("httpx")' in src and 'getLogger("httpcore")' in src
```

(Source-assertion tests are the pattern this repo already uses for `test_no_module_hardcodes_a_model_literal`; note the getsource-flake caveat — a concurrent edit, not a regression, if they fail oddly.)

- [ ] **Step 2:** RED. **Step 3:** implement: redaction substitution pass (iterate the snapshot, `text.replace(v, "[REDACTED]")`, ≥8-char floor); in `chat_service` wrap the tool_error collector content: `"content": _redact_secrets_in_str(str(detail or exc)[:1000])` (locate by the string; there is exactly one site) — and same wrap in `chat_tools.py`'s vision failure f-string; in `bootstrap.install_file_logging`, after the root-handler install add `logging.getLogger("httpx").setLevel(logging.WARNING)` + same for `httpcore`, with a one-line comment (base_url userinfo is rejected at validation; this is the belt).
- [ ] **Step 4:** GREEN + `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_llm_provider_seam.py -q`.
- [ ] **Step 5:** commit `fix(chat): widen secret redaction to all managed values; cap httpx logging`.

---

### Task 5: Model constants move + legacy mapping helper

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (locate `DEFAULT_MODEL: str =`), `pypsa-gui/backend/services/llm_config.py`
- Test: `pypsa-gui/backend/tests/test_chat_models.py` (REPLACE the two ALLOWED_MODELS tests — the one sanctioned existing-test edit in this plan, named here per spec verification item 6), `tests/test_llm_config.py` (append)

**Interfaces:**
- `chat_service.DEFAULT_MODEL` / `OPUS_MODEL` become `from services.llm_config import DEFAULT_MODEL, OPUS_MODEL` (pinned names keep working; `llm_config` imports nothing from `chat_service` — no cycle). `ALLOWED_MODELS` is DELETED.
- New in `llm_config`: `resolve_legacy_model(model: str | None) -> LLMProfile` — `DEFAULT_MODEL`→builtin sonnet, `OPUS_MODEL`→builtin opus, None/unknown→`resolve_active()` (unknown logs one warning).

- [ ] **Step 1: failing tests:** in `test_chat_models.py` DELETE `test_allowed_models_is_exactly_those_two` and `test_an_unknown_model_is_not_in_the_allow_list`; ADD:

```python
def test_legacy_model_strings_resolve_to_builtin_profiles():
    from services import llm_config
    assert llm_config.resolve_legacy_model("claude-sonnet-5").id == "anthropic-sonnet"
    assert llm_config.resolve_legacy_model("claude-opus-5").id == "anthropic-opus"
    # unknown → active profile, not a refusal (documented passthrough contract)
    assert llm_config.resolve_legacy_model("gpt-4").id == llm_config.resolve_active().id
    assert llm_config.resolve_legacy_model(None).id == llm_config.resolve_active().id
```

- [ ] **Step 2:** RED. **Step 3:** implement; delete `ALLOWED_MODELS` (grep repo-wide for references first — the seam left none in code, the frontend comment in `api/chat.ts:22` is Task 12's). Keep `test_models_are_the_current_generation` passing via the re-export.
- [ ] **Step 4:** GREEN + `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_models.py pypsa-gui/backend/tests/test_llm_config.py -q`.
- [ ] **Step 5:** commit `refactor(chat): model constants move to llm_config; legacy mapping replaces ALLOWED_MODELS`.

---

### Task 6: Profile-constructed providers + event-type validation

**Files:**
- Modify: `pypsa-gui/backend/services/llm_anthropic.py` (add `provider_for_profile`), `llm_openai_compat.py` (M6 items), `llm_provider.py` (M5), `chat_service.py` (construction site in `_run_turn_body` — locate `provider = llm_anthropic.AnthropicProvider(client)`)
- Test: `tests/test_llm_provider_seam.py` (append)

**Interfaces:**
- New module-level factory in `chat_service` (kept there so the `client=`/`provider=` injection seams stay untouched):

```python
def _provider_for_profile(profile, client=None):
    """(provider, error_kind|None). anthropic wire + ANTHROPIC_API_KEY slot →
    the existing _build_anthropic_client path (byte-identical zero-config
    behaviour, incl. missing_api_key). anthropic wire + other slot →
    anthropic.Anthropic(api_key=<slot value>). openai wire →
    OpenAICompatProvider(base_url, api_key=<slot value or None>);
    auth=="bearer" with empty slot → (None, "missing_api_key")."""
```

- M5: `LLMEvent.__post_init__` raises `ValueError` on `type not in EVENT_TYPES` (typo'd events currently fall through silently as pings).
- M6 in `llm_openai_compat`: send BOTH `max_tokens` and `max_completion_tokens` unless Task 2's research recorded a reason not to; synthesize `tool_use_start` when the FIRST delta for an index lacks `id` (use `f"call_{index}"` as fallback id so a tool block never ships `id: ""`). T6: a user turn mixing plain text + tool_result emits BOTH the tool messages and a user message (drop the `and not results` guard).

- [ ] **Step 1: failing tests:**

```python
def test_llm_event_rejects_unknown_type():
    import pytest
    from services.llm_provider import LLMEvent
    with pytest.raises(ValueError):
        LLMEvent(type="text_deltaa")


def test_provider_for_profile_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    from services import chat_service, llm_config
    ollama = llm_config.LLMProfile(
        id="ol", label="ol", preset="custom", wire="openai",
        base_url="http://localhost:11434/v1", model="m", tools=True,
        vision=False, auth="none", fallback_model=None, max_output_tokens=None)
    p, err = chat_service._provider_for_profile(ollama)
    assert err is None and p.name == "openai-compat"
    bearer = llm_config.LLMProfile(
        id="oa", label="oa", preset="openai", wire="openai",
        base_url="https://h.example/v1", model="m", tools=True, vision=False,
        auth="bearer", fallback_model=None, max_output_tokens=None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p, err = chat_service._provider_for_profile(bearer)
    assert p is None and err == "missing_api_key"


def test_openai_compat_idless_tool_delta_gets_synthetic_id():
    # scripted SSE where tool_calls index 0 never carries an id
    ...  # same MockTransport pattern as the existing openai tests; assert
    # one tool_use_start with tool_use_id == "call_0" and the final block
    # carries id "call_0", and payload includes max_completion_tokens
```

(Write the third test fully using `_sse_bytes` — copy the existing scripted-hello structure.)

- [ ] **Step 2:** RED. **Step 3:** implement. `_provider_for_profile` resolves the key value via `os.environ.get(profile.key_env)` when `key_env` is set (bootstrap already applied `user.env`). For custom anthropic-wire slots pass `api_key=` explicitly — note in a comment this is the ONE sanctioned kwarg use (the env-read invariant covers only the built-in slot).
- [ ] **Step 4:** GREEN + whole seam file.
- [ ] **Step 5:** commit `feat(chat): providers constructed from profiles; event-type validation; openai-compat hardening`.

---

### Task 7: Session binding + turn-path integration

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (`ChatSession` dataclass — locate `model: str = DEFAULT_MODEL` in the class; `run_turn`/`_run_turn_body`; `append_turn` record build — locate `turn_record` ), `pypsa-gui/backend/routers/chat.py` (locate `if body.model:` and the `has_explicit_script` branch; `StreamRequest` model; history route — locate `get_or_create_session(` in the history handler; import route required-tuple UNTOUCHED)
- Test: new `tests/test_chat_profile_binding.py`

**Interfaces:**
- `ChatSession` gains `profile_id: str | None = None` and `bound_wire: str | None = None`.
- `StreamRequest` gains `profile_id: str | None = None` (legacy `model` field stays).
- Router, BEFORE the script branch: resolve `body.profile_id or resolve_legacy_model(body.model)` → profile; if session unbound, bind (`session.profile_id/bound_wire`, and set `session.model = profile.model` — same-wire rebind allowed: rebind + model update; cross-wire → pass `wire_conflict=True` into the generator, which emits `error{profile_switch_requires_new_chat}` + `session_done` as its FIRST frames and returns (never a 4xx — the client discards non-2xx bodies). The old `if body.model: session.model = body.model` line is DELETED (subsumed).
- `run_turn` provider resolution: when `provider is None and client is None` → `_provider_for_profile(resolve_profile(session.profile_id), ...)`; the injected `client=`/`provider=` seams unchanged (tests bind no profile → zero-config path).
- `session_init` payload gains `profile_id` + `profile_label`; its `tool_count` reports the count actually sent (0 when tools omitted — Task 8). `model` key stays (pinned).
- Turn record (`append_turn` caller) gains `"profile_id": session.profile_id or <resolved builtin id>`; history rehydration resolves the recorded `profile_id` (else legacy `model`) via `llm_config`, binds the minted session, and DROPS non-portable blocks when the resolved wire is `openai` (thinking/redacted_thinking/image/document blocks filtered during replay-append) — locate the history handler's `append_history_message` loop.
- Per-profile `max_output_tokens`: request uses `profile.max_output_tokens or MAX_OUTPUT_TOKENS_PER_TURN`.
- A8 generalisation: replace the `session.model == OPUS_MODEL` guard with `fallback := profile.fallback_model` (non-None, one attempt — hoist `model_fallback_used` initialisation ABOVE the outer `while True:` loop so it is turn-scoped, which the review found was previously only guaranteed by the model mutation); on fire: `session.model = fallback`, frame unchanged + gains `profile_id`.

- [ ] **Step 1: failing tests** — `test_chat_profile_binding.py` with FakeProvider (no key needed):

```python
"""Session↔profile binding (spec §Turn path). Uses FakeProvider throughout."""
# fixtures: appdata tmp dir (as test_llm_config), a saved ollama-like openai
# profile, plus chat_service._reset_sessions_for_tests() around each test.

def test_session_binds_active_profile_and_session_init_reports_it(...):
    # stream via routers/chat.py TestClient with profile_id omitted →
    # session_init frame carries profile_id == active id and profile_label

def test_cross_wire_switch_mid_session_emits_typed_frame(...):
    # bind session on anthropic-sonnet (FakeProvider turn), then POST /stream
    # naming the openai profile → first frames are
    # error{profile_switch_requires_new_chat} + session_done; session.messages
    # unchanged; NO 4xx

def test_same_wire_rebind_updates_model(...):
    # anthropic-sonnet → anthropic-opus mid-session: allowed, session.model
    # becomes OPUS_MODEL, no error frame

def test_legacy_model_field_still_works(...):
    # body {"model": "claude-opus-5"} on a fresh session binds anthropic-opus

def test_turn_record_carries_profile_id_and_rehydration_binds(...):
    # run a FakeProvider turn with a bound project, read chat.jsonl record →
    # profile_id present; call GET /history → minted session has
    # profile_id/bound_wire set

def test_rehydration_into_openai_wire_drops_thinking_blocks(...):
    # write a chat.jsonl record whose assistant blocks include a thinking
    # block and profile_id of an openai profile → after /history, the
    # rehydrated session.messages contain no thinking blocks

def test_a8_fallback_uses_profile_fallback_model(...):
    # profile with fallback_model set + scripted rate_limited ProviderErrors
    # (monkeypatch retry delays to 0, MAX_STREAM_RETRIES small) → exactly one
    # model_fallback frame, session.model == fallback
```

Write these fully (the e2e file's FakeProvider + TestClient patterns are the template; copy its fixture usage, not its content).

- [ ] **Step 2:** RED. **Step 3:** implement per Interfaces. **Step 4:** GREEN + behaviour gate: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_e2e.py pypsa-gui/backend/tests/test_chat_sse.py pypsa-gui/backend/tests/test_chat_profile_binding.py pypsa-gui/backend/tests/test_llm_provider_seam.py -q` — existing suites unmodified.
- [ ] **Step 5:** commit `feat(chat): sessions bind resolved profiles; wire enforcement; durable profile_id`.

---

### Task 8: Capability enforcement + prompt split

**Files:**
- Modify: `pypsa-gui/backend/services/chat_service.py` (locate `_DOMAIN_GUIDE`, `_SOLVER_ERROR_DECODER`, `_PRICE_CONGESTION_GUIDE`, `_NEXT_STEP_RUBRIC`, `_build_system_prompt`, the attachment block, the request build in `_run_turn_body`), `pypsa-gui/backend/services/chat_tools.py` (locate `reconstruct_network_from_image`'s client build + `model=DEFAULT_MODEL`)
- Test: `tests/test_chat_profile_binding.py` (append) + named updates to `test_chat_e2e.py` prompt pins (locate the test asserting `_DOMAIN_GUIDE in system` — the SECOND sanctioned existing-test edit, per spec verification item 6)

**Interfaces:**
- Each guide constant splits into `_X_FACTS` + `_X_CHAINING` (both module-level; `_X = _X_FACTS + _X_CHAINING` preserved as the byte-identical concatenation so default prompts are unchanged). `_build_system_prompt(session, live_meta, include_tools: bool = True)` — `False` assembles FACTS halves only and omits the confirmation-card contract paragraph.
- `tools: false` profile → request carries `tools=[]`, `tools_stable=False`, trimmed prompt, `session_init.tool_count == 0`.
- Vision: before each provider call, scan the OUTBOUND `messages` for `image`/`document` blocks; if any and `profile.vision is False` → `error{capability_unsupported, message: fixed string naming the capability + profile label}` + `session_done` (covers fresh attachments AND in-session replay). PDF (`document`) blocks additionally require `wire == "anthropic"` even when `vision` is true → same frame with a PDF-specific fixed string.
- `reconstruct_network_from_image`: resolve the active profile; `vision False` → the tool returns the structured 503-style error with `error_kind="capability_unsupported"` (matching its existing error shape — read the surrounding handler); anthropic wire → existing client path but `model=profile.model`; openai wire + vision → construct via `_provider_for_profile` and drive a non-streaming completion... **NO — YAGNI:** for this plan the tool supports anthropic-wire profiles only; openai-wire + vision returns the capability error with a fixed "vision network reconstruction currently requires an Anthropic profile" string. (Recorded limitation, one string, no new vision wire path.)

- [ ] **Step 1: failing tests** (append to `test_chat_profile_binding.py`; write fully):

```python
def test_toolless_profile_sends_no_tools_and_trimmed_prompt(...):
    # profile tools=False + FakeProvider → fake.requests[0].tools == [] and
    # "CHAIN get_results" not in the system text; session_init.tool_count == 0

def test_vision_false_blocks_replayed_image_blocks(...):
    # seed session.messages with an image block from a "previous turn";
    # stream on a vision=False profile → capability_unsupported BEFORE any
    # provider call (fake.requests stays empty)

def test_default_prompt_bytes_unchanged():
    # _X_FACTS + _X_CHAINING == the pre-split constant for all four guides
    # (pin with a hash captured in Step 1 against current master)
```

- [ ] **Step 2:** RED. **Step 3:** implement; update the named e2e prompt pins minimally. **Step 4:** GREEN + behaviour gate as Task 7. **Step 5:** commit `feat(chat): capability-honest degradation — tools/vision enforcement, prompt split`.

---

### Task 9: Settings + profiles routes, health, connection test

**Files:**
- Modify: `pypsa-gui/backend/routers/chat.py` (after the existing api-key routes — locate `_require_super_admin`), `pypsa-gui/backend/tests/conftest.py` (autouse cleanup)
- Test: new `tests/test_llm_settings_api.py`

**Interfaces (route contract — frontend Task 12 consumes exactly this):**

```
GET    /api/chat/settings/llm            → {profiles: [ProfileOut], active_profile_id, presets: [...]}   (super-admin)
PUT    /api/chat/settings/llm/profiles/{id}   body ProfileIn (NO key_env, NO key)  → ProfileOut          (super-admin)
DELETE /api/chat/settings/llm/profiles/{id}   → {ok, active_profile_id}; clears namespaced slot          (super-admin)
PUT    /api/chat/settings/llm/profiles/{id}/key   body {value}; DELETE same path   → key status          (super-admin)
POST   /api/chat/settings/llm/active     body {profile_id}                          → {active_profile_id} (super-admin)
POST   /api/chat/settings/llm/profiles/{id}/test  → {verdict: ok|unreachable|unauthorized|model_not_found|invalid_request, latency_ms|null, models: [...]|null}   (super-admin)
GET    /api/chat/profiles                → {profiles: [{id,label,wire}], active_profile_id}              (any authenticated user)
```

`ProfileOut` = profile fields + `key_required: bool` + `key_present: bool` + `key_hint: str|null` — never a value, never a full base_url in errors. `GET /chat/health` gains `active_profile: {id,label,wire}` and `chat_ready: bool`; `anthropic_api_key_present`/`default_model` keep today's exact semantics.

Connection test: `max_tokens=1` non-streaming completion through `_provider_for_profile` (add a tiny `probe()` helper per provider or drive `stream()` and take the first event — implementer's choice, but the verdict mapping must go through the existing error kinds; 404-ish model errors → `model_not_found` verdict). Best-effort `GET {base}/models` (or Anthropic models list) → `models`; failures → `null`. Fixed verdict strings; host:port at most.

Conftest: autouse fixture `_clean_llm_profiles` that yields then `unlink(missing_ok=True)`s `llm-profiles.json` under the session app-data dir (the shared-dir leak the spec's verification item 2 names).

- [ ] **Step 1: failing tests** — write `test_llm_settings_api.py` fully: super-admin gate on every settings route (member → 403); member gate on `/chat/profiles` (anonymous → 401/403 per repo convention — copy from an existing member-gated route test); create-profile → appears in GET; `key_env` in a PUT body is REJECTED (422 — model forbids it); key PUT → `key_present` true + hint, GET responses carry no value (assert the raw response text does not contain the key); DELETE profile clears its `PYPSA_GUI_LLM_KEY__*` slot (via `app_secrets.get_stored`); active POST validates id; test endpoint verdicts via `httpx.MockTransport` monkeypatched into `_provider_for_profile`'s openai path (parametrize 200/401/404/timeout→ok/unauthorized/model_not_found/unreachable); health gains the new fields with no profiles file (zero-config synthesis).
- [ ] **Step 2:** RED. **Step 3:** implement (pydantic models with `extra="forbid"` for ProfileIn). **Step 4:** GREEN + `test_chat_api_key_settings.py` unmodified-pass. **Step 5:** commit `feat(chat): LLM settings + profiles routes, connection test, health additions`.

---

### Task 10: `set_active_profile` tool + read-side awareness

**Files:**
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py` (append to `TOOLS`), `chat_tools.py` (handler + `DISPATCHERS` entry), `chat_service.py` (system-prompt context — locate `_format_live_network_meta` usage in `_build_system_prompt`)
- Test: `tests/test_chat_profile_binding.py` (append)

**Interfaces:**
- Tool: `set_active_profile`, `input_schema {profile_id: string, required}`, description carries `Safety: destructive` marker (an existing `DESTRUCTIVE_TIERS` member — grep `DESTRUCTIVE_TIERS` and reuse the tier the marker-grep test recognises) and states "takes effect when you start a new chat". Handler validates via `llm_config` (unknown id → structured error), calls `set_active`, returns `{ok, active_profile_id, note}`.
- System prompt gains one short block: active profile label + configured labels + two-line switch procedure (built from `llm_config`, byte-stable within a turn).

- [ ] **Step 1: failing tests:** parity (`len(TOOLS) == len(DISPATCHERS)` — already exists, will fail on a half-added tool); `set_active_profile` requires confirmation (drive the confirmation lifecycle exactly as an existing destructive-tool e2e does — copy the deny + approve pattern); approve → `llm_config.resolve_active().id` changed; deny → unchanged; unknown id → tool_error, active unchanged.
- [ ] **Step 2:** RED. **Step 3:** implement. **Step 4:** GREEN + behaviour gate. **Step 5:** commit `feat(chat): set_active_profile tool (confirmation-gated) + profile awareness`.

---

### Task 11: Backend close-out — legacy surfaces + smoke + gate

**Files:**
- Modify: `pypsa-gui/backend/routers/local_settings.py` (docstring/comment only: names the pane as the built-in Anthropic profile's key field), `backend/smoke/run_chat_smoke.py` (locate `--model` — add `--profile`, keep `--model` mapping through `resolve_legacy_model`), `pypsa-gui/CHATBOT.md` (+ stale comment in `chat_service` model-constants block)
- Test: existing suites + optional live smoke

- [ ] **Step 1:** smoke flag: RED = `run_chat_smoke.py --profile` unknown-arg error; add the flag. Docs edits are the named comment/doc exemption.
- [ ] **Step 2:** live Ollama path: with `PYPSA_GUI_TEST_OLLAMA_URL` set, `test_seam_against_live_local_endpoint` (exists) plus a new skipif-gated binding test streaming one turn through a REAL saved openai profile. Skipped-when-absent must show in output.
- [ ] **Step 3:** full backend gate `pixi run gui-tests` — exit 0 required before any frontend task.
- [ ] **Step 4:** commit `chore(chat): legacy surface notes, smoke --profile, docs`.

---

### Task 12: Frontend API layer

**Files:**
- Modify: `pypsa-gui/frontend/src/api/chat.ts`; Create: `src/api/llmSettings.ts`
- Test: `src/api/llmSettings.test.ts` (new), existing `api/*.test.ts` untouched

**Interfaces (exact — Tasks 13-15 import these):**

```ts
// chat.ts: ChatModel type deleted; every use → string. ChatStreamRequest gains
// profile_id?: string. ChatTurn gains profile_id?: string. ChatHealth gains
// active_profile?: { id: string; label: string; wire: 'anthropic' | 'openai' };
// chat_ready?: boolean. Fix the stale :22 comment (points at llm_config now).

// llmSettings.ts
export interface LLMProfileOut { id: string; label: string; preset: string;
  wire: 'anthropic' | 'openai'; base_url: string | null; model: string;
  tools: boolean; vision: boolean; auth: 'bearer' | 'none';
  fallback_model: string | null; max_output_tokens: number | null;
  key_required: boolean; key_present: boolean; key_hint: string | null }
export interface LLMSettingsPayload { profiles: LLMProfileOut[];
  active_profile_id: string; presets: PresetOut[] }
export interface ChatProfilesPayload {
  profiles: Array<{ id: string; label: string; wire: 'anthropic' | 'openai' }>;
  active_profile_id: string }
export async function getLLMSettings(): Promise<LLMSettingsPayload>
export async function putLLMProfile(id: string, body: LLMProfileIn): Promise<LLMProfileOut>
export async function deleteLLMProfile(id: string): Promise<{ ok: boolean; active_profile_id: string }>
export async function putLLMProfileKey(id: string, value: string): Promise<KeyStatus>
export async function deleteLLMProfileKey(id: string): Promise<KeyStatus>
export async function postLLMActive(profileId: string): Promise<{ active_profile_id: string }>
export async function postLLMTest(id: string): Promise<TestVerdict>
export async function getChatProfiles(): Promise<ChatProfilesPayload>   // member-level
```

- [ ] Steps: RED (type test importing the new module), implement thin axios wrappers over the Task 9 contract (`skipErrorToast` on the member-level GET, matching `getApiKeySettings`'s rationale), GREEN (`npm test -- llmSettings`), commit `feat(gui): LLM settings API client; ChatModel union retired`.

---

### Task 13: chatStore + ChatPanel switching UX

**Files:**
- Modify: `src/store/chatStore.ts` (locate `model:` state + `setModel`), `src/components/ChatPanel.tsx` (locate the `<select` with model options; the stream request build — locate `model,` in the POST body; `handleFrame`'s switch; the hydration effect — locate `getChatHistory()` in the mount effect; Clear button handler)
- Test: `src/store/chatStore.test.ts` section + `src/components/ChatPanel.profile.test.tsx` (new; mock `api/llmSettings` and extend the `importOriginal` mock of `api/chat`)

**Interfaces:**
- Store: `model: ChatModel` → `profileId: string | null` (default `null` = server's active); `setModel` → `setProfileId`; new `startNewChat()` — nulls `sessionId`, clears `messages/pending/toolProgress/error/usage`, sets `suppressHydrationOnce: true`; the hydration effect consumes and clears that flag before calling `getChatHistory()`.
- Request build: include `profile_id` ONLY when `profileId !== null`; the legacy `model` field is no longer sent.
- Dropdown: options from `getChatProfiles()` via a `useChatProfiles` hook (react-query if present in the codebase — match how other components fetch; otherwise a `useEffect`+state fetch with refetch on `session_init` and after settings mutations). Render: disabled placeholder until loaded; `value = profileId ?? active_profile_id`; `disabled={streaming}`; `max-w-[9rem] truncate` + `title`. Picking same-wire → `setProfileId` silently. Picking cross-wire (compare `wire` against current selection's wire) → inline confirm row "Switching to <label> starts a new chat · [Switch] [Cancel]"; Switch → `setProfileId(id)` + `startNewChat()`.
- New `handleFrame` cases: `model_fallback` → append a system-styled line "`from_model` → `to_model` (rate limited)"; `thinking` → accumulate into a muted collapsible block (minimal: a `<details>` per assistant turn, matching existing message styling); `session_init` → adopt `profile_id` into the store when `profileId === null` stays null (display only — do not pin).

- [ ] **Step 1: failing tests** (write fully in the new test file): `startNewChat` clears sessionId + suppresses one hydration; request body carries `profile_id` only when set and never `model`; dropdown renders profile labels from the mocked payload and disables while streaming; cross-wire pick shows the confirm row and only commits on Switch; `model_fallback` frame renders the line; `thinking` frames render inside a details element.
- [ ] **Step 2:** RED (`npm test -- ChatPanel.profile`). **Step 3:** implement. **Step 4:** GREEN + ALL existing ChatPanel suites (`npm test -- ChatPanel`) — they mock `api/chat` with `importOriginal`, so add `getChatProfiles` to their mock surface ONLY if a test fails for missing mock (touch minimally, note each). **Step 5:** commit `feat(gui): profile dropdown, startNewChat, fallback + thinking frames`.

---

### Task 14: ErrorBanner KIND_COPY + new kinds

**Files:**
- Modify: `src/components/ChatPanel.tsx` (locate the kind→title map, the negated fall-through list, and the tool_error allowlist — three sites collapse into one `KIND_COPY` map)
- Test: `src/components/ChatPanel.profile.test.tsx` (append)

**Interfaces:** `KIND_COPY: Record<string, { title: string; body?: string; action?: 'open-settings' | 'new-chat' }>` — entries for every kind the three old structures covered (verify by diffing the union of the old lists against the new map keys in a test) plus: `unreachable` ("Could not reach the model endpoint. Is it running? Check Settings.", open-settings), `capability_unsupported` (body from the frame message, open-settings), `profile_switch_requires_new_chat` ("This model needs a fresh chat.", new-chat → `startNewChat()`), broadened `missing_api_key` (body names the active profile; the inline ApiKeySetup still renders for the built-in Anthropic profile — Task 15). `open-settings` action = `uiStore.setSlidePanel('settings')` + `requestSettingsSection('assistant-model')` (Task 15's uiStore addition — stub the call behind a feature-safe optional until Task 15 lands, or order Task 15 first at execution if simpler; state the choice in the report).

- [ ] Steps: RED (tests for the three new kinds' rendering + a completeness test that every previously-handled kind still has copy), implement, GREEN + existing suites, commit `feat(gui): unified KIND_COPY error rendering with guidance actions`.

---

### Task 15: AssistantModelSettings + hosting, gating, banner generalisation

**Files:**
- Create: `src/components/AssistantModelSettings.tsx`
- Modify: `src/store/uiStore.ts` (add `requestSettingsSection`/`clearSettingsSectionRequest` — copy the `requestResultsTab` request/clear pair's shape exactly, locate it by name), `src/pages/LocalSettings.tsx` (host the section ABOVE its `if (state == null) return null` early-return — restructure so AssistantModelSettings renders whenever `/chat/settings/llm` is reachable, even when local-settings 404s; the pinned "renders nothing on web" test updates to "renders only the assistant section on web when llm-settings reachable" — the THIRD sanctioned existing-test edit), `src/layout/Sidebar.tsx` + `src/components/CommandPalette.tsx` (settings nav gates: visible when EITHER surface is reachable — locate `useLocalSettingsAvailable`), `src/components/ApiKeySetup.tsx` (+ its full-factory test mock in the same commit)
- Test: `src/components/AssistantModelSettings.test.tsx` (new), `LocalSettings.test.tsx` (the named edit), `ApiKeySetup.test.tsx` (mock surface extension)

**Component contract (AssistantModelSettings):** section anchor id `assistant-model`; consumes `getLLMSettings()`; renders profile list (radio = active, `postLLMActive` on change), per-profile: label, model, wire badge, key status (hint / "uses ANTHROPIC_API_KEY" / "no key needed"), Test button → verdict line (latency on ok; fixed copy per verdict; model suggestions datalist fed from `models`), key field for `auth==='bearer'` (masked input, save/clear via `putLLMProfileKey`/`deleteLLMProfileKey` — reuse ApiKeySetup's masking/hint pattern), delete (confirm via the repo's ConfirmDialog if present on trunk by then, else `window.confirm` matching existing settings-pane style); "Add model" → preset picker (from `presets`) prefilling the form + Custom (label, base_url, model free text with suggestions, tools/vision toggles); scrolls itself into view when `settingsSectionRequest === 'assistant-model'` then clears the request (the `requestResultsTab` consume pattern).
**ApiKeySetup generalisation:** trigger prop stays the error frame; body reads the active profile from `getChatHealth()` (NEW import → extend the factory mock in the same commit); built-in Anthropic active → keep today's inline field verbatim; otherwise text + "Open settings" deep-link. Update the Anthropic-specific copy sites listed in the spec (`ApiKeySetup.tsx`, `pages/LocalSettings.tsx` heading/confirm, `api/localSettings.ts` placeholder) — grep `sk-ant` in `frontend/src` and account for every hit in the report.

- [ ] Steps: RED (component tests: renders profiles from mock, active radio posts, key field never displays a stored value, test-verdict rendering per mocked verdict, section-request scroll+clear, web-mode hosting — llm-settings reachable + local-settings 404 renders the section), implement, GREEN + full frontend `npm test`, commit `feat(gui): AssistantModelSettings pane, settings deep-link, banner generalisation`.

---

### Task 16: Full gates + docs close-out

- [ ] `pixi run gui-tests` → exit 0 (paste summary).
- [ ] `cd pypsa-gui/frontend && npm test` → exit 0; `npx tsc --noEmit` → clean (trunk convention gates all three).
- [ ] `git diff --stat <plan-start>..HEAD -- pypsa-gui/backend/tests` — list which existing test files changed; must be exactly the three sanctioned edits (test_chat_models.py, the e2e prompt pins, LocalSettings.test.tsx + mock-surface files named in reports).
- [ ] Update `CHATBOT.md` setup section: profiles + presets + per-profile keys replace the single-key story (keep the legacy env-var path documented).
- [ ] Commit `docs(gui): CHATBOT.md — multi-provider setup`.

## Self-Review (done at planning time)

- **Spec coverage:** profile store+built-ins (T1), catalogue+packaging (T2), secrets rule+enumerator (T3), redaction+logger caps+leak sites (T4), constants+legacy mapping (T5), profile-built providers+M5/M6/T6 (T6), binding+wire enforcement+records+A8+max_output_tokens (T7), capabilities+prompt split+vision sub-call (T8), routes+test+health+conftest (T9), tool+awareness (T10), legacy surfaces+smoke (T11), API layer (T12), switching UX+frames (T13), KIND_COPY (T14), settings pane+gating+banner (T15), gates+docs (T16). Spec's Out-of-scope respected (no per-user profiles, no tool curation, no reasoning knobs).
- **Sanctioned existing-test edits, exhaustive:** `test_chat_models.py` (T5), `test_chat_e2e.py` prompt pins (T8), `LocalSettings.test.tsx` (T15), mock-surface extensions where a NEW import demands them (T13/T15, named per file in reports). Anything else failing = seam defect.
- **Type consistency:** `LLMProfile` field list identical in T1 interface, T6/T7 constructor calls, T9 ProfileOut, T12 TS mirror. Route paths in T9 == T12 client. `requestSettingsSection('assistant-model')` string matches T15's anchor id. `resolve_legacy_model` defined T5, consumed T7/T11.
- **Known risk, stated:** T7 and T13 are the two integration-heavy tasks; their briefs tell implementers to re-locate every site by name because the pending `feature/local-app-impl` trunk merge will move anchors (it touches `ChatPanel.tsx` and `routers/projects.py`). If that merge lands mid-plan, re-run the behaviour gates before continuing.
