"""
LLM profile store (spec 2026-08-13, plan 2026-08-14, Task 1).

Imports only stdlib, `app_paths` and `services.app_secrets`, deliberately —
same rule `local_settings.py` documents at its own top: `main.py` and the
provider seam must be able to read profile config before the router graph
exists, so this module may not import `chat_service`, any `llm_*` provider
module, or anything else that could pull the app graph in behind it.

WHAT THIS OWNS. The list of LLM connection profiles (built-in Anthropic
presets plus anything the user has added), which one is active, and the
validation that keeps a saved profile safe to actually connect with. It does
NOT own talking to a provider — that is `services/llm_provider.py` and its
`llm_anthropic` / `llm_openai_compat` implementations, already merged and
untouched by this module.

TWO BUILT-INS, ALWAYS PRESENT. `anthropic-sonnet` and `anthropic-opus` exist
whether or not `llm-profiles.json` exists, because the app must have a working
default even on a machine that has never touched Settings. They are
synthesized in code, not read from the file, so a corrupt or hand-edited file
can never remove them or repoint their ids at something else — a file entry
that reuses a built-in id is rejected by `save_profiles` and simply skipped
(with a warning) if it is somehow already on disk when `load_profiles` reads
it back.

`key_env` IS DERIVED, NEVER STORED. It names the environment variable a
profile's key lives in (e.g. `ANTHROPIC_API_KEY`, or a per-profile
`PYPSA_GUI_LLM_KEY__<SLUG>` for a custom endpoint) so nothing above this layer
ever handles or persists a client-supplied key or env-var name — the value in
`os.environ` stays the only place a secret lives, matching `app_secrets`'s own
rule.

NO CACHING. `load_profiles` re-reads the file on every call. A cached result
would make per-test app-data redirection (`PYPSAGUI_APP_DATA_DIR`) unreliable
across tests that share a process, and in production the file only changes
through `save_profiles` in this same process anyway, so re-reading costs
nothing that matters.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app_paths
from services import app_secrets  # noqa: F401 — see module docstring import rule

logger = logging.getLogger(__name__)

# Moved here from `chat_service` (Task 5 re-imports these rather than
# defining them) so the profile store — not the chat harness — is the single
# owner of what "the default model" means.
DEFAULT_MODEL: str = "claude-sonnet-5"
OPUS_MODEL: str = "claude-opus-5"

BUILTIN_SONNET_ID = "anthropic-sonnet"
BUILTIN_OPUS_ID = "anthropic-opus"
_BUILTIN_IDS: frozenset[str] = frozenset([BUILTIN_SONNET_ID, BUILTIN_OPUS_ID])

_FILENAME = "llm-profiles.json"
_FILE_VERSION = 1

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,48}$")
_WIRE_VALUES = frozenset(["anthropic", "openai"])
_AUTH_VALUES = frozenset(["bearer", "none"])
_CREDENTIAL_QUERY_KEYS = frozenset(["key", "token", "api_key"])


class ProfileValidationError(ValueError):
    """A profile (or the set being saved) fails validation."""


@dataclass(frozen=True)
class LLMProfile:
    id: str  # slug [a-z0-9-]{1,48}
    label: str
    preset: str  # catalogue id (see `load_presets`) or "custom"
    wire: str  # "anthropic" | "openai"
    base_url: str | None  # None = wire's default endpoint
    model: str  # free text, non-empty
    tools: bool  # capabilities flattened onto the profile — a frozen
    vision: bool  # dataclass can't hold a nested mutable "capabilities" object
    auth: str  # "bearer" | "none"
    fallback_model: str | None
    max_output_tokens: int | None

    @property
    def key_env(self) -> str | None:
        """
        The environment variable this profile's API key lives in, or None
        when `auth` is `"none"` (a local endpoint that takes no key).

        Derived, not stored: nothing serializes this, and nothing reads it
        back out of the file — it is recomputed from `preset`/`id` every
        time, which is also why a profile can never be pointed at an
        arbitrary env var by editing JSON on disk.
        """
        if self.auth == "none":
            return None
        return derive_key_env(self.id, self.preset)


def _preset_catalogue() -> dict[str, dict]:
    return {entry["id"]: entry for entry in load_presets() if isinstance(entry, dict) and "id" in entry}


def derive_key_env(profile_id: str, preset: str) -> str | None:
    """
    The env var a profile's key comes from.

    A cataloged preset (from `presets.json`, Task 2) declares its own
    `key_env` (or None, for a preset whose auth is "none") — that declaration
    wins. `"custom"` (or any preset id not in the catalogue, e.g. because
    `presets.json` has not shipped yet) falls back to a per-profile name
    derived from its slug, so two custom profiles never collide on one
    variable.

    The two built-in ids are hardcoded here rather than left to depend on
    `presets.json`: they must resolve correctly even in a dev checkout that
    predates Task 2, when the catalogue file does not exist yet and
    `load_presets()` returns `[]`.
    """
    if profile_id in _BUILTIN_IDS:
        return "ANTHROPIC_API_KEY"
    if preset != "custom":
        entry = _preset_catalogue().get(preset)
        if entry is not None:
            return entry.get("key_env")
    return "PYPSA_GUI_LLM_KEY__" + profile_id.upper().replace("-", "_")


def profiles_path() -> Path:
    return app_paths.app_data_dir() / _FILENAME


def _builtin_profiles() -> list[LLMProfile]:
    return [
        LLMProfile(
            id=BUILTIN_SONNET_ID,
            label="Claude Sonnet",
            preset="anthropic-sonnet",
            wire="anthropic",
            base_url=None,
            model=DEFAULT_MODEL,
            tools=True,
            vision=True,
            auth="bearer",
            fallback_model=None,
            max_output_tokens=None,
        ),
        LLMProfile(
            id=BUILTIN_OPUS_ID,
            label="Claude Opus",
            preset="anthropic-opus",
            wire="anthropic",
            base_url=None,
            model=OPUS_MODEL,
            tools=True,
            vision=True,
            auth="bearer",
            fallback_model=DEFAULT_MODEL,
            max_output_tokens=None,
        ),
    ]


def _validate_profile(profile: LLMProfile) -> None:
    if not _SLUG_RE.match(profile.id):
        raise ProfileValidationError(
            f"profile id {profile.id!r} must match [a-z0-9-]{{1,48}}"
        )
    if profile.wire not in _WIRE_VALUES:
        raise ProfileValidationError(
            f"profile {profile.id!r}: wire must be one of {sorted(_WIRE_VALUES)}, got {profile.wire!r}"
        )
    if not profile.model:
        raise ProfileValidationError(f"profile {profile.id!r}: model must not be empty")
    if profile.auth not in _AUTH_VALUES:
        raise ProfileValidationError(
            f"profile {profile.id!r}: auth must be one of {sorted(_AUTH_VALUES)}, got {profile.auth!r}"
        )
    if profile.base_url is not None:
        _validate_base_url(profile.id, profile.base_url)


def _validate_base_url(profile_id: str, base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ProfileValidationError(
            f"profile {profile_id!r}: base_url scheme must be http or https, got {parts.scheme!r}"
        )
    if "@" in parts.netloc:
        # Userinfo (`user:pass@host`) would smuggle a credential into a URL
        # that gets logged, displayed in Settings, and written to disk in
        # plaintext — the same class of leak `app_secrets` exists to avoid.
        raise ProfileValidationError(
            f"profile {profile_id!r}: base_url must not contain userinfo (user:pass@host)"
        )
    query = parse_qs(parts.query)
    leaked = _CREDENTIAL_QUERY_KEYS & set(query)
    if leaked:
        raise ProfileValidationError(
            f"profile {profile_id!r}: base_url query must not contain credential-shaped "
            f"parameters ({', '.join(sorted(leaked))})"
        )


def _profile_from_dict(data: dict) -> LLMProfile:
    # Only the declared fields are ever read out of a JSON entry — an unknown
    # key is ignored rather than rejected, so a future field can be added
    # without corrupting a file written by an older build.
    return LLMProfile(
        id=data["id"],
        label=data["label"],
        preset=data["preset"],
        wire=data["wire"],
        base_url=data.get("base_url"),
        model=data["model"],
        tools=bool(data.get("tools", False)),
        vision=bool(data.get("vision", False)),
        auth=data["auth"],
        fallback_model=data.get("fallback_model"),
        max_output_tokens=data.get("max_output_tokens"),
    )


def _profile_to_dict(profile: LLMProfile) -> dict:
    return {
        "id": profile.id,
        "label": profile.label,
        "preset": profile.preset,
        "wire": profile.wire,
        "base_url": profile.base_url,
        "model": profile.model,
        "tools": profile.tools,
        "vision": profile.vision,
        "auth": profile.auth,
        "fallback_model": profile.fallback_model,
        "max_output_tokens": profile.max_output_tokens,
    }


def _read_file() -> dict | None:
    """
    The raw parsed file, or None on any absence/corruption. NEVER raises.

    Follows `local_settings.read_settings`'s never-raise discipline exactly:
    a missing file is normal (first run); an unreadable, non-UTF-8,
    non-JSON, or non-object file is a degraded state to warn about and
    ignore, never a reason `load_profiles` should crash the app that calls it
    at startup.
    """
    path = profiles_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("llm profiles: %s could not be read; ignoring it", path)
        return None
    except UnicodeDecodeError:
        # UnicodeDecodeError is a ValueError subclass but NOT an OSError
        # subclass, so it needs its own branch or it would raise straight
        # out of a function documented as "NEVER raises" — the same trap
        # `local_settings.read_settings` calls out for the same reason.
        logger.warning("llm profiles: %s is not valid UTF-8; ignoring it", path)
        return None

    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("llm profiles: %s is not valid JSON; ignoring it", path)
        return None
    if not isinstance(data, dict):
        logger.warning("llm profiles: %s is not a JSON object; ignoring it", path)
        return None
    return data


def load_profiles() -> tuple[list[LLMProfile], str]:
    """
    `(profiles, active_id)`. NEVER raises.

    Built-ins are always first (`BUILTIN_SONNET_ID`, `BUILTIN_OPUS_ID`) and
    always present, synthesized in code rather than read from disk. File
    entries are appended after them; an entry cannot reuse a built-in id
    (rejected, logged, skipped), and any entry that is malformed or fails
    validation is logged and skipped rather than aborting the whole load —
    one bad profile must not take every other profile down with it.
    """
    builtins = _builtin_profiles()
    active_id = BUILTIN_SONNET_ID

    data = _read_file()
    if data is None:
        return builtins, active_id

    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list):
        logger.warning("llm profiles: %s has no profiles list; ignoring it", profiles_path())
        return builtins, active_id

    profiles = list(builtins)
    seen_ids = set(_BUILTIN_IDS)
    for entry in raw_profiles:
        if not isinstance(entry, dict):
            logger.warning("llm profiles: skipping a non-object profile entry")
            continue
        try:
            profile = _profile_from_dict(entry)
            if profile.id in _BUILTIN_IDS:
                raise ProfileValidationError(
                    f"profile id {profile.id!r} is reserved for a built-in profile"
                )
            if profile.id in seen_ids:
                raise ProfileValidationError(f"duplicate profile id {profile.id!r}")
            _validate_profile(profile)
        except (KeyError, TypeError, ProfileValidationError) as exc:
            logger.warning("llm profiles: skipping an invalid profile entry: %s", exc)
            continue
        profiles.append(profile)
        seen_ids.add(profile.id)

    stored_active = data.get("active_profile_id")
    if isinstance(stored_active, str) and stored_active in seen_ids:
        active_id = stored_active

    return profiles, active_id


def save_profiles(profiles: list[LLMProfile], active_id: str) -> None:
    """
    Validate every profile, then write `llm-profiles.json` at 0600.

    Validates-then-writes: if ANY profile fails validation, or reuses a
    built-in id, or `active_id` names nothing in `profiles` or the built-ins,
    nothing is written — a bad save must not clobber a working file with a
    half-valid one.

    File profiles only: the two built-ins are never written (they are
    synthesized by `load_profiles` on every read), so this function raises if
    `profiles` contains a built-in id — accepting it silently would make it
    look like a built-in came from the file, which is exactly the ambiguity
    Task 1 promises callers cannot happen.
    """
    seen_ids: set[str] = set()
    for profile in profiles:
        if profile.id in _BUILTIN_IDS:
            raise ProfileValidationError(
                f"profile id {profile.id!r} is reserved for a built-in profile and cannot be saved"
            )
        if profile.id in seen_ids:
            raise ProfileValidationError(f"duplicate profile id {profile.id!r}")
        _validate_profile(profile)
        seen_ids.add(profile.id)

    if active_id not in seen_ids and active_id not in _BUILTIN_IDS:
        raise ProfileValidationError(f"active_profile_id {active_id!r} is not a known profile")

    payload = {
        "version": _FILE_VERSION,
        "active_profile_id": active_id,
        "profiles": [_profile_to_dict(p) for p in profiles],
    }

    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Mode applied at open time, not write-then-chmod, matching
    # `app_secrets._write_managed`: the alternative leaves a window where the
    # file (which may hold a base_url a user considers private, even though
    # never a raw key) is briefly world-readable under the process umask.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    # O_CREAT only sets the mode on a file it CREATES; re-assert it for the
    # overwrite case, which is the common one once a file already exists.
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — Windows / exotic filesystems
        logger.debug("could not chmod %s", path, exc_info=True)


def resolve_profile(profile_id: str | None) -> LLMProfile:
    """
    The profile named by `profile_id`, or the active profile if `profile_id`
    is None or names nothing in the current store.

    Legacy model strings (pre-profile chat sessions that stored a bare model
    name rather than a profile id) are NOT handled here — that translation is
    Task 7's, in the chat harness that still has the legacy value to look at.
    """
    profiles, active_id = load_profiles()
    by_id = {p.id: p for p in profiles}
    if profile_id is not None and profile_id in by_id:
        return by_id[profile_id]
    return by_id[active_id]


def resolve_active() -> LLMProfile:
    return resolve_profile(None)


def set_active(profile_id: str) -> None:
    """Persist `profile_id` as active. Raises `ProfileValidationError` if unknown."""
    profiles, current_active = load_profiles()
    by_id = {p.id: p for p in profiles}
    if profile_id not in by_id:
        raise ProfileValidationError(f"unknown profile id {profile_id!r}")
    file_profiles = [p for p in profiles if p.id not in _BUILTIN_IDS]
    save_profiles(file_profiles, profile_id)


def load_presets() -> list[dict]:
    """
    The bundled preset catalogue, or `[]`.

    `presets.json` ships in Task 2 — on a checkout that predates it (or any
    build that omits it) this returns `[]` rather than raising, and
    `derive_key_env` falls back to the per-profile custom-key-env scheme.
    """
    path = Path(__file__).resolve().parent.parent / "presets.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("llm presets: %s is not valid JSON; ignoring it", path)
        return []
    if not isinstance(data, list):
        logger.warning("llm presets: %s is not a JSON array; ignoring it", path)
        return []
    return [entry for entry in data if isinstance(entry, dict)]
