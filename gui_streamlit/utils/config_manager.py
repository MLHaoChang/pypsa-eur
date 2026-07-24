from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_project_root() -> Path:
    return PROJECT_ROOT


def load_default_config() -> dict:
    with open(PROJECT_ROOT / "config" / "config.default.yaml") as f:
        return yaml.safe_load(f)


def load_user_config() -> dict:
    path = PROJECT_ROOT / "config" / "config.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def get_active_config() -> dict:
    """Return default config deep-merged with user overrides."""
    base = load_default_config()
    _deep_update(base, load_user_config())
    return base


def save_user_config(cfg: dict) -> None:
    path = PROJECT_ROOT / "config" / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _deep_update(base: dict, overrides: dict) -> None:
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
