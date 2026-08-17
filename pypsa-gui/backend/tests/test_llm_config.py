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
