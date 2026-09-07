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
    "https://h.example/v1?API_KEY=sk-live",      # credential query, uppercase key
    "https://h.example/v1?Token=abc",            # credential query, mixed case key
    "https://h.example/v1?KEY=abc",              # credential query, uppercase key
])
def test_base_url_validation_rejects_credentials(appdata, base_url):
    from services import llm_config
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config.save_profiles([llm_config.LLMProfile(
            id="x", label="x", preset="custom", wire="openai",
            base_url=base_url, model="m", tools=True, vision=False,
            auth="bearer", fallback_model=None, max_output_tokens=None)], "x")


def test_non_bool_capability_field_is_skipped_builtins_still_load(appdata):
    from services import llm_config
    llm_config.profiles_path().write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [{
            "id": "bad-tools", "label": "Bad", "preset": "custom", "wire": "openai",
            "base_url": None, "model": "m", "tools": "false", "vision": True,
            "auth": "none", "fallback_model": None, "max_output_tokens": None,
        }],
    }), encoding="utf-8")
    profiles, active = llm_config.load_profiles()
    ids = [p.id for p in profiles]
    assert "bad-tools" not in ids
    assert ids == ["anthropic-sonnet", "anthropic-opus"]
    assert active == "anthropic-sonnet"


def test_resolve_unknown_is_refused_and_set_active_moves_the_pointer(appdata):
    """
    INVERTED BY C-4. This test used to assert
    `resolve_profile("nope").id == "anthropic-sonnet"` — i.e. it pinned the
    silent fall-through as correct, which is how the defect survived review.
    An unresolvable selection must not render as a successful turn on a
    different provider; `frontend/src/api/chat.ts` already documented the
    refusing contract. The `set_active` half below was always right and is
    kept unchanged.
    """
    from services import llm_config
    with pytest.raises(llm_config.ProfileNotConfiguredError):
        llm_config.resolve_profile("nope")
    llm_config.set_active("anthropic-opus")
    assert llm_config.resolve_active().id == "anthropic-opus"


def test_presets_catalogue_shape():
    from services import llm_config
    presets = llm_config.load_presets()
    ids = {p["id"] for p in presets}
    assert ids == {"anthropic", "openai", "moonshot", "dashscope",
                   "ollama", "lmstudio"}
    for p in presets:
        assert set(p) == {"id", "label", "wire", "base_url", "auth",
                          "key_env", "token_param", "tools", "vision",
                          "suggested_models", "help"}
        assert p["wire"] in ("anthropic", "openai")
        # C-2 — every preset declares which completion-length parameter its
        # endpoint wants, and exactly one of the two spellings is legal.
        assert p["token_param"] in ("max_tokens", "max_completion_tokens")
        if p["auth"] == "none":
            assert p["key_env"] is None
        else:
            assert p["key_env"] == p["key_env"].upper()

    by_id = {p["id"]: p for p in presets}
    # The one preset that MUST carry the newer spelling: current OpenAI
    # models refuse the presence of `max_tokens` outright (C-2). Everything
    # else — local servers and the OpenAI-compatible vendors — takes the
    # original, which is also the fallback for `custom`.
    assert by_id["openai"]["token_param"] == "max_completion_tokens"
    for other in ("moonshot", "dashscope", "ollama", "lmstudio"):
        assert by_id[other]["token_param"] == "max_tokens"


def test_local_presets_are_keyless():
    from services import llm_config
    by_id = {p["id"]: p for p in llm_config.load_presets()}
    assert by_id["ollama"]["auth"] == "none"
    assert by_id["lmstudio"]["auth"] == "none"


def test_bearer_preset_locks_base_url_to_prevent_key_exfiltration(appdata):
    # Task 1 review's binding security constraint: a cataloged bearer preset
    # (whose key_env resolves to a SHARED provider key) must not be
    # combinable with an attacker-chosen base_url, or "catalogued preset +
    # divergent base_url" exfiltrates that shared key to the wrong host.
    from services import llm_config
    evil = llm_config.LLMProfile(
        id="openai-evil", label="Evil", preset="openai", wire="openai",
        base_url="https://evil.example/v1", model="gpt-5.6-terra",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config.save_profiles([evil], "openai-evil")

    ok = llm_config.LLMProfile(
        id="openai-evil", label="Evil", preset="openai", wire="openai",
        base_url=None, model="gpt-5.6-terra",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([ok], "openai-evil")
    profiles, _ = llm_config.load_profiles()
    assert {p.id for p in profiles} >= {"openai-evil"}


# ─────────────────────────────────────────────────────────────────────────
# Profile-id anchoring — the guard at its DEFINITION, not through a caller.
#
# `_SLUG_RE` was `^[a-z0-9-]{1,48}$` matched with `.match()`. In Python `$`
# also matches immediately BEFORE a trailing newline, so `"custom\n"` was
# accepted as a valid profile id. That is the same defect class this branch
# already fixed once in `app_secrets._LLM_KEY_SLOT_RE` (which is `\A`/`\Z`
# anchored for exactly this reason) — probing it through one call site cannot
# see it, so it is asserted here against the predicate itself.
#
# The consequence is not cosmetic: `derive_key_env` builds
# `PYPSA_GUI_LLM_KEY__CUSTOM\n`, which `app_secrets.is_managed_key` correctly
# refuses, so `routers/chat._profile_out` raises `SecretValueError` out of
# `app_secrets.status()` and `GET /chat/settings/llm` 500s. `load_profiles`
# promises the opposite ("one bad profile must not take every other profile
# down with it") — see the route-level test in test_llm_settings_api.py.
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "custom\n",       # trailing newline — what `$` let through
        "custom\n\n",
        "\ncustom",       # leading newline
        "cus\ntom",       # embedded newline
        "Custom",         # uppercase
        "custom key",     # space
        "custom_key",     # underscore is not in the class
        "",               # empty
        "a" * 49,         # over the length bound
    ],
)
def test_slug_predicate_refuses_anything_but_the_documented_class(bad_id):
    """The regex itself must refuse everything outside `[a-z0-9-]{1,48}`."""
    from services import llm_config
    assert not llm_config._SLUG_RE.match(bad_id), (
        f"_SLUG_RE accepted {bad_id!r}, which is outside [a-z0-9-]{{1,48}}"
    )


def test_validate_profile_refuses_an_id_with_a_trailing_newline(appdata):
    """
    A trailing newline must be rejected by validation, not merely by the
    downstream `is_managed_key` check it happens to trip over.
    """
    from services import llm_config
    bad = llm_config.LLMProfile(
        id="custom\n", label="x", preset="custom", wire="openai",
        base_url="https://example.invalid/v1", model="m",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_profile(bad)


def test_load_profiles_skips_a_file_entry_whose_id_has_a_trailing_newline(appdata):
    """
    A hand-edited file entry with a newline-terminated id is skipped, and the
    surviving profiles still load — `load_profiles`'s stated guarantee.
    """
    from services import llm_config
    llm_config.profiles_path().write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "custom\n", "label": "bad", "preset": "custom",
             "wire": "openai", "base_url": "https://example.invalid/v1",
             "model": "m", "tools": True, "vision": False, "auth": "bearer",
             "fallback_model": None, "max_output_tokens": None},
            {"id": "good-one", "label": "good", "preset": "custom",
             "wire": "openai", "base_url": "https://example.invalid/v1",
             "model": "m", "tools": True, "vision": False, "auth": "bearer",
             "fallback_model": None, "max_output_tokens": None},
        ],
    }), encoding="utf-8")
    profiles, _active = llm_config.load_profiles()
    ids = {p.id for p in profiles}
    assert "custom\n" not in ids, "an id with a trailing newline was loaded"
    assert "good-one" in ids, "the valid sibling entry was dropped too"


def test_every_loaded_profiles_key_env_is_a_managed_key(appdata):
    """
    CROSS-MODULE INVARIANT. `routers/chat._profile_out` calls
    `app_secrets.status(profile.key_env)`, which RAISES for a name
    `is_managed_key` refuses. So every `key_env` `load_profiles` can produce
    must be a managed name — otherwise the settings pane 500s on read.

    Asserted over what the store actually yields for a hostile file, rather
    than over a hand-picked id, so a future change to `derive_key_env` or to
    the slug class is caught here even if neither test above is updated.
    """
    from services import app_secrets, llm_config
    llm_config.profiles_path().write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "custom\n", "label": "bad", "preset": "custom",
             "wire": "openai", "base_url": "https://example.invalid/v1",
             "model": "m", "tools": True, "vision": False, "auth": "bearer",
             "fallback_model": None, "max_output_tokens": None},
        ],
    }), encoding="utf-8")
    profiles, _active = llm_config.load_profiles()
    for profile in profiles:
        key_env = profile.key_env
        if key_env is None:
            continue
        assert app_secrets.is_managed_key(key_env), (
            f"profile {profile.id!r} derives key_env {key_env!r}, which "
            f"app_secrets refuses — app_secrets.status() will raise on it"
        )


# ─────────────────────────────────────────────────────────────────────────
# Preset spoofing — the gap the handover records as "not yet attacked".
#
# The shared-key exfiltration primitive the branch must not have is:
#   preset that INHERITS a shared provider key  +  attacker-chosen base_url
# `_validate_preset_base_url_lock` blocks the exact-match case. The question
# left open was whether a near-miss preset id (case, whitespace, a zero-width
# char, a homoglyph) could slip past the LOCK while still inheriting the
# SHARED key from `derive_key_env`.
#
# It cannot, and the reason is structural rather than lucky: both functions
# resolve the preset through the SAME exact-string `_preset_catalogue().get()`,
# so a spoofed id misses in both — the lock is skipped, but the profile also
# drops to its own private `PYPSA_GUI_LLM_KEY__<SLUG>` slot, which is not a
# shared credential. Spoofing the preset COSTS you the shared key.
#
# That safety is entirely a consequence of the two lookups agreeing, which is
# exactly the kind of coupling a later "helpful" change breaks on one side
# only (e.g. making preset resolution case-insensitive in `derive_key_env`
# alone). This test states the invariant so that drift is caught.
# ─────────────────────────────────────────────────────────────────────────

_SHARED_PROVIDER_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY",
})


@pytest.mark.parametrize(
    "spoofed_preset",
    [
        "Anthropic",        # capitalised
        "OPENAI",           # upper-cased
        "openai ",          # trailing space
        " openai",          # leading space
        "openai​",     # zero-width space
        "openai\n",         # trailing newline
        "moonsh0t",         # digit homoglyph
        "unknown-preset",   # simply not catalogued
    ],
)
def test_a_spoofed_preset_can_never_inherit_a_shared_provider_key(
    appdata, spoofed_preset,
):
    """
    A profile whose preset does not EXACTLY name a catalogue entry must fall
    back to its own namespaced slot — never a shared provider key — no matter
    what base_url it carries.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="evil", label="e", preset=spoofed_preset, wire="openai",
        base_url="https://attacker.example/v1", model="m",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)

    try:
        llm_config._validate_profile(profile)
    except llm_config.ProfileValidationError:
        return  # Refused outright — also a safe outcome.

    # It validated, so the preset/base_url lock did not fire. The key slot
    # must therefore be private to this profile.
    assert profile.key_env not in _SHARED_PROVIDER_KEYS, (
        f"preset {spoofed_preset!r} passed validation with an attacker "
        f"base_url AND inherited the shared key {profile.key_env!r} — this "
        f"is the exfiltration primitive _validate_preset_base_url_lock exists "
        f"to prevent"
    )


@pytest.mark.parametrize(
    "preset,wire",
    [
        # CORRECTED: every param used to be built with `wire="openai"`, but
        # the `anthropic` preset declares `wire="anthropic"` — so once
        # `_validate_preset_wire_lock` was added (S-M2) it ran FIRST and
        # raised, and the [anthropic] case proved nothing about the base_url
        # lock it names. A mutation audit confirmed it: with the base_url lock
        # fully disabled, [openai], [moonshot] and [dashscope] failed while
        # [anthropic] PASSED. Each preset now carries its OWN declared wire,
        # so only the lock under test can be what refuses.
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("moonshot", "openai"),
        ("dashscope", "openai"),
    ],
)
def test_an_exact_bearer_preset_is_locked_to_its_own_base_url(appdata, preset, wire):
    """The sibling half: an EXACT catalogue match cannot be repointed at all."""
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="evil", label="e", preset=preset, wire=wire,
        base_url="https://attacker.example/v1", model="m",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)
    # Precondition: this preset really does carry a shared key, so the test
    # is asserting about the dangerous case and not a vacuous one.
    assert profile.key_env in _SHARED_PROVIDER_KEYS
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_profile(profile)


# ─────────────────────────────────────────────────────────────────────────
# S-M1 — the preset lock and the key derivation disagreed about which
# presets carry a shared credential.
#
#   _validate_preset_base_url_lock  gated on  entry["auth"] != "bearer"
#   derive_key_env                  decided from  entry["key_env"]
#
# Those are two different questions. The invariant survived only because no
# SHIPPED preset has `auth != "bearer"` together with a non-null `key_env` —
# i.e. it held by accident of the current catalogue, and was fail-open for
# the next preset added. The lock exists to stop a shared provider key being
# sent to an operator-chosen host, so it must key on the same thing the key
# derivation does: whether this preset hands the profile a shared `key_env`.
#
# Asserted against a synthetic catalogue entry, because the point is exactly
# the preset that does not exist yet.
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def _catalogue_with_a_keyed_non_bearer_preset(monkeypatch):
    """A preset whose `auth` is not "bearer" but which still names a shared key."""
    from services import llm_config
    entry = {
        "id": "future-vendor",
        "label": "Future Vendor",
        "wire": "openai",
        "base_url": "https://api.future-vendor.example/v1",
        "auth": "header",          # not "bearer" — the lock used to skip on this
        "key_env": "OPENAI_API_KEY",  # ...but it still hands over a SHARED key
        "tools": True,
        "vision": False,
    }
    shipped = llm_config.load_presets()  # captured BEFORE patching
    monkeypatch.setattr(llm_config, "load_presets", lambda: [*shipped, entry])
    return entry


def test_a_non_bearer_preset_that_names_a_shared_key_is_still_locked(
    appdata, _catalogue_with_a_keyed_non_bearer_preset,
):
    """
    A preset that hands over a SHARED key must be locked to its own base_url,
    whatever its `auth` value says.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="evil", label="e", preset="future-vendor", wire="openai",
        base_url="https://attacker.example/v1", model="m",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)

    # Precondition: this profile really does inherit the shared key, so the
    # assertion below is about the dangerous case and not a vacuous one.
    assert profile.key_env == "OPENAI_API_KEY"

    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_profile(profile)


def test_a_keyless_preset_is_still_free_to_be_repointed(
    appdata, monkeypatch,
):
    """
    THE SIBLING PATH. Tightening the lock must not start refusing a preset
    that hands over NO shared key (`key_env: null`, e.g. Ollama/LM Studio) —
    repointing one of those at another host leaks nothing, and forbidding it
    would break the documented local-endpoint workflow.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="my-ollama", label="Ollama elsewhere", preset="ollama",
        wire="openai", base_url="http://192.168.1.50:11434/v1", model="qwen3",
        tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None)
    assert profile.key_env is None
    llm_config._validate_profile(profile)  # must not raise


# ─────────────────────────────────────────────────────────────────────────
# S-M2 — a third-party key was shipped to Anthropic.
#
# The anthropic branch builds `anthropic.Anthropic(api_key=key_value)` and
# never reads `profile.base_url` (chat_service.py:1750). Nothing validated
# `wire` against `preset`, so `preset="openai", wire="anthropic"` resolved
# `key_env=OPENAI_API_KEY` and sent that live key to `api.anthropic.com` on
# every turn — and because `base_url` is IGNORED on that branch, Settings
# showed a URL that was never contacted, so the operator could not tell.
#
# A catalogued preset declares its own `wire`; a profile may not contradict
# it. `preset="custom"` declares none and stays free to pick either.
# ─────────────────────────────────────────────────────────────────────────


def test_a_profile_may_not_contradict_its_presets_declared_wire(appdata):
    """`preset="openai"` + `wire="anthropic"` sends OPENAI_API_KEY to Anthropic."""
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="wire-mismatch", label="m", preset="openai", wire="anthropic",
        base_url=None, model="claude-sonnet-5",
        tools=True, vision=True, auth="bearer",
        fallback_model=None, max_output_tokens=None)

    # Precondition: this really does inherit OpenAI's shared key.
    assert profile.key_env == "OPENAI_API_KEY"

    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_profile(profile)


@pytest.mark.parametrize(
    "preset,wire",
    [
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("moonshot", "openai"),
        ("dashscope", "openai"),
        ("ollama", "openai"),
        ("lmstudio", "openai"),
    ],
)
def test_every_shipped_preset_accepts_its_own_declared_wire(appdata, preset, wire):
    """THE SIBLING PATH — the rule must not refuse any legitimate combination."""
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="ok-one", label="ok", preset=preset, wire=wire,
        base_url=None, model="m", tools=True, vision=False,
        auth="none", fallback_model=None, max_output_tokens=None)
    llm_config._validate_profile(profile)  # must not raise


@pytest.mark.parametrize("wire", ["anthropic", "openai"])
def test_a_custom_preset_declares_no_wire_and_may_pick_either(appdata, wire):
    """`custom` owns its own key slot, so neither wire leaks a shared key."""
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="mine", label="mine", preset="custom", wire=wire,
        base_url="https://example.invalid/v1", model="m",
        tools=True, vision=False, auth="bearer",
        fallback_model=None, max_output_tokens=None)
    llm_config._validate_profile(profile)  # must not raise


# ─────────────────────────────────────────────────────────────────────────
# W-5 — `load_profiles` is documented "NEVER raises", and did.
#
# `urlsplit("http://[::1")` raises a BARE `ValueError` ("Invalid IPv6 URL").
# The per-entry guard in `load_profiles` caught
# `(KeyError, TypeError, ProfileValidationError)` — and because
# `ProfileValidationError` IS a `ValueError` subclass, that reads as though
# ValueErrors are handled. A bare one is not, and escaped.
#
# Blast radius is the whole chat surface: every turn, `GET /settings/llm`,
# `GET /chat/profiles` and `/history` all call `load_profiles`, and so does
# every route that could DELETE the offending entry — so there was no in-app
# repair. Filed previously as "S-L2: malformed IPv6 base_url -> 500", which
# understated it.
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_url",
    ["http://[::1", "http://[fe80::1", "https://[not-ipv6"],
)
def test_a_malformed_url_is_a_validation_error_not_a_bare_valueerror(bad_url):
    """At the predicate: the parser's ValueError must be translated."""
    from services import llm_config
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_base_url("p", bad_url)


def test_one_unparsable_entry_does_not_take_the_whole_file_down(appdata):
    """`load_profiles` keeps its NEVER-raises promise, and its siblings load."""
    from services import llm_config
    llm_config.profiles_path().write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "broken", "label": "bad", "preset": "custom",
             "wire": "openai", "base_url": "http://[::1", "model": "m",
             "tools": True, "vision": False, "auth": "none",
             "fallback_model": None, "max_output_tokens": None},
            {"id": "good-two", "label": "good", "preset": "custom",
             "wire": "openai", "base_url": "http://localhost:11434/v1",
             "model": "m", "tools": True, "vision": False, "auth": "none",
             "fallback_model": None, "max_output_tokens": None},
        ],
    }), encoding="utf-8")

    profiles, _active = llm_config.load_profiles()   # must not raise
    ids = {p.id for p in profiles}
    assert "broken" not in ids
    assert "good-two" in ids, "the valid sibling entry was dropped too"
    assert {"anthropic-sonnet", "anthropic-opus"} <= ids


@pytest.mark.parametrize("bad_value", [8080, True, 3.5])
def test_a_non_string_base_url_is_a_validation_error_not_an_attributeerror(bad_value):
    """
    W-5 was INCOMPLETE. It translated the `ValueError` `urlsplit` raises on a
    malformed *string*, but an unquoted JSON scalar — `"base_url": 8080` — makes
    `urlsplit` raise `AttributeError` ('int' object has no attribute 'decode'),
    which neither the new catch nor the widened per-entry guard covers. The
    parametrised test that shipped with W-5 only used strings, so it was
    structurally incapable of seeing this.
    """
    from services import llm_config
    with pytest.raises(llm_config.ProfileValidationError):
        llm_config._validate_base_url("p", bad_value)


@pytest.mark.parametrize("bad_value", [8080, True, 3.5])
def test_a_non_string_base_url_entry_is_skipped_not_fatal(appdata, bad_value):
    """`load_profiles` keeps its NEVER-raises promise for wrong JSON types too."""
    from services import llm_config
    llm_config.profiles_path().write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "typed-wrong", "label": "bad", "preset": "custom",
             "wire": "openai", "base_url": bad_value, "model": "m",
             "tools": True, "vision": False, "auth": "none",
             "fallback_model": None, "max_output_tokens": None},
            {"id": "good-three", "label": "good", "preset": "custom",
             "wire": "openai", "base_url": "http://localhost:11434/v1",
             "model": "m", "tools": True, "vision": False, "auth": "none",
             "fallback_model": None, "max_output_tokens": None},
        ],
    }), encoding="utf-8")
    profiles, _active = llm_config.load_profiles()   # must not raise
    ids = {p.id for p in profiles}
    assert "typed-wrong" not in ids
    assert "good-three" in ids


# ─────────────────────────────────────────────────────────────────────────
# C-14 — a write erased every profile entry that failed validation on load.
#
# `load_profiles` skips an unloadable entry (correct: one bad profile must not
# take the others down). `save_profiles` rewrites the whole file from the list
# it is handed (correct: it is the authority on what a profile is). Each half
# is right; together they silently delete data.
#
# WORSE than filed in two ways the original missed. It is not only "any
# profile write": `set_active` does the same load→filter→save, so merely
# SWITCHING the active profile destroys them. And `set_active_profile` is a
# CHAT TOOL, so the assistant can trigger the deletion on the user's behalf.
#
# Realistic trigger: a user downgrades a build, or hand-edits one entry wrong.
# The next model switch deletes it permanently, with only a `logger.warning`
# nobody reads — no backup, no 422, nothing in the UI.
# ─────────────────────────────────────────────────────────────────────────


def _file_with_one_unloadable_entry():
    return {
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "good-one", "label": "Good", "preset": "custom",
             "wire": "openai", "base_url": "http://localhost:11434/v1",
             "model": "m", "tools": True, "vision": False, "auth": "none",
             "fallback_model": None, "max_output_tokens": None},
            # A wire this build does not know — e.g. written by a newer build.
            {"id": "future-one", "label": "From a newer build",
             "preset": "custom", "wire": "gemini", "base_url": None,
             "model": "gemini-3", "tools": True, "vision": True,
             "auth": "bearer", "fallback_model": None,
             "max_output_tokens": None},
        ],
    }


def _raw_ids(path):
    return [e.get("id") for e in json.loads(path.read_text())["profiles"]]


def test_switching_the_active_profile_does_not_delete_unloadable_entries(appdata):
    """The path the finding understated: `set_active`, reachable from a tool."""
    from services import llm_config

    path = llm_config.profiles_path()
    path.write_text(json.dumps(_file_with_one_unloadable_entry()), encoding="utf-8")

    loaded = {p.id for p in llm_config.load_profiles()[0]}
    assert "good-one" in loaded and "future-one" not in loaded, (
        "fixture is wrong: the entry under test must be skipped on load"
    )

    llm_config.set_active("anthropic-opus")

    assert "future-one" in _raw_ids(path), (
        f"switching the active profile deleted an unloadable entry: "
        f"{_raw_ids(path)}"
    )
    assert "good-one" in _raw_ids(path)


def test_saving_a_profile_does_not_delete_unloadable_entries(appdata):
    """The path as filed: an ordinary profile write."""
    from services import llm_config

    path = llm_config.profiles_path()
    path.write_text(json.dumps(_file_with_one_unloadable_entry()), encoding="utf-8")

    profiles, active = llm_config.load_profiles()
    file_profiles = [p for p in profiles if p.id not in ("anthropic-sonnet",
                                                        "anthropic-opus")]
    llm_config.save_profiles(file_profiles, active)

    assert "future-one" in _raw_ids(path), (
        f"a profile write deleted an unloadable entry: {_raw_ids(path)}"
    )


def test_writing_over_an_unloadable_id_replaces_it_rather_than_duplicating(appdata):
    """
    DISCRIMINATION. Preserving must not resurrect an entry the operator is
    deliberately overwriting, nor leave two entries sharing one id.
    """
    from services import llm_config

    path = llm_config.profiles_path()
    path.write_text(json.dumps(_file_with_one_unloadable_entry()), encoding="utf-8")

    fixed = llm_config.LLMProfile(
        id="future-one", label="Repaired", preset="custom", wire="openai",
        base_url="http://localhost:11434/v1", model="m",
        tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([fixed], "anthropic-sonnet")

    ids = _raw_ids(path)
    assert ids.count("future-one") == 1, f"id duplicated on repair: {ids}"
    assert {p.id for p in llm_config.load_profiles()[0]} >= {"future-one"}


def test_a_failed_profile_write_does_not_destroy_the_file(appdata, monkeypatch):
    """
    The sibling of the `app_secrets` A3 finding, in the other store this
    branch introduced. `save_profiles` also opened the live file `O_TRUNC`
    and wrote in place, so a failure mid-write left `llm-profiles.json`
    truncated — taking every profile with it.
    """
    import os as _os

    from services import llm_config

    path = llm_config.profiles_path()
    path.write_text(json.dumps(_file_with_one_unloadable_entry()), encoding="utf-8")
    before = path.read_text()

    real_fdopen = _os.fdopen
    armed = {"on": True}

    def _explode(fd, *a, **kw):
        handle = real_fdopen(fd, *a, **kw)
        if not armed["on"]:
            return handle
        original_write = handle.write

        def _boom(_data):
            original_write("")
            raise OSError(28, "No space left on device")

        handle.write = _boom
        return handle

    monkeypatch.setattr(_os, "fdopen", _explode)
    with pytest.raises(OSError):
        llm_config.save_profiles([], "anthropic-sonnet")
    armed["on"] = False

    assert path.read_text() == before, "a failed write truncated the profile store"


def test_a_leftover_temp_file_from_a_killed_write_does_not_brick_saving(appdata):
    """
    The atomic write creates its temp with `O_EXCL`. A SIGKILL or power loss
    between that open and the `os.replace` leaves the temp behind — which is
    precisely the crash the atomic write exists to survive. If the next write
    then refuses because the temp is in the way, one crash permanently bricks
    every profile save with no route to recovery from inside the app.
    """
    from services import llm_config
    p = llm_config.LLMProfile(
        id="ollama-local", label="Ollama (local)", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([p], "ollama-local")

    # Leftovers a killed write could plausibly have left. The fixed name is
    # the regression this test was written against; the second covers a fix
    # that merely derives ONE name some other way, which bricks identically.
    path = llm_config.profiles_path()
    for stale in (path.name + ".tmp", path.name + ".ab12cd.tmp"):
        path.with_name(stale).write_text("partial", encoding="utf-8")

    llm_config.save_profiles([p], "anthropic-sonnet")
    _, active = llm_config.load_profiles()
    assert active == "anthropic-sonnet"

    # And the write must not have been "atomic" by clobbering a stale temp it
    # had no business touching — the point is a name nothing else can hold.
    assert path.with_name(path.name + ".tmp").read_text() == "partial"


def test_an_entry_with_an_unhashable_id_does_not_break_saving(appdata):
    """
    The C-14 preservation asks `entry.get("id") in written_ids` of every raw
    entry. A hand-edited file whose `id` is a list or a dict makes that
    membership test raise `TypeError` — turning a store this build merely
    could not READ into one it can no longer WRITE. `load_profiles` already
    tolerates the same entry, so the two halves must agree.
    """
    from services import llm_config

    path = llm_config.profiles_path()
    path.write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": ["not", "a", "string"], "label": "Broken", "preset": "custom",
             "wire": "openai", "base_url": None, "model": "m", "tools": True,
             "vision": False, "auth": "none", "fallback_model": None,
             "max_output_tokens": None},
        ],
    }), encoding="utf-8")

    # Precondition: the load side survives it. The write side must too.
    llm_config.load_profiles()

    llm_config.set_active("anthropic-opus")
    assert json.loads(path.read_text())["active_profile_id"] == "anthropic-opus"


def test_deleting_a_profile_does_not_resurrect_a_shadowed_duplicate(appdata):
    """
    A hand-edited file can hold two entries sharing an id. `load_profiles`
    keeps the first and SKIPS the second, so "an entry the load skipped" now
    describes a live, valid profile the user cannot see.

    Deleting that profile is the case where it bites: the delete rewrites the
    file without that id, so the shadowed twin is no longer one the caller is
    replacing. Preserve it and the delete does not delete — the profile comes
    back on the next load wearing the SECOND entry's settings, which is worse
    than the data loss C-14 fixed: silently restoring a base_url the operator
    just removed.
    """
    from services import llm_config

    path = llm_config.profiles_path()
    base = {"label": "Dup", "preset": "custom", "wire": "openai",
            "base_url": "http://localhost:11434/v1", "tools": True,
            "vision": False, "auth": "none", "fallback_model": None,
            "max_output_tokens": None}
    path.write_text(json.dumps({
        "version": 1,
        "active_profile_id": "anthropic-sonnet",
        "profiles": [
            {"id": "twice", "model": "first", **base},
            {"id": "twice", "model": "second", **base},
        ],
    }), encoding="utf-8")

    profiles, _ = llm_config.load_profiles()
    visible = {p.id: p for p in profiles}
    assert visible["twice"].model == "first", "fixture: the load must shadow one"

    # The delete: rewrite the store with that profile removed.
    llm_config.save_profiles([], "anthropic-sonnet")

    assert "twice" not in _raw_ids(path), (
        f"deleting a profile left a shadowed twin behind: {_raw_ids(path)}"
    )
    assert "twice" not in {p.id for p in llm_config.load_profiles()[0]}, (
        "the deleted profile came back from its shadowed duplicate"
    )
