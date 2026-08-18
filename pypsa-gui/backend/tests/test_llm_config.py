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


def test_resolve_unknown_falls_back_to_active(appdata):
    from services import llm_config
    assert llm_config.resolve_profile("nope").id == "anthropic-sonnet"
    llm_config_profiles, _ = llm_config.load_profiles()
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
