"""
Where the Anthropic API key actually comes from, and whether the stores agree.

Written to reproduce the two-store collision described in
docs/superpowers/findings/2026-08-05-api-key-store-collision.md: `master` and
`feature/local-app-impl` each grew a store for the same secret, the merge
stacked both, and the second publisher was dead behind the first's
`if os.environ.get(...)` guard. It is kept, and inverted, because the same
three cases are what you want to see when someone reports "I saved my key and
chat still says it is missing".

Runs entirely against throwaway app-data directories. It never reads, writes or
prints the real user's key.

    python smoke/repro_api_key_collision.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import local_settings  # noqa: E402
from services import app_secrets  # noqa: E402

USER_ENV_KEY = "sk-ant-FROM-user-env-AAAA"
JSON_KEY = "sk-ant-FROM-local-settings-json-BBBB"
SHELL_KEY = "sk-ant-FROM-the-shell-CCCC"


def _fresh_appdata(prefix: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["PYPSAGUI_APP_DATA_DIR"] = str(tmp)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    # `_SHELL_NAMES` is module state captured by bootstrap_environment; reset it
    # so each case below starts from "the shell said nothing".
    app_secrets._SHELL_NAMES = frozenset()
    return tmp


def _write_legacy(appdata: Path, key: str) -> None:
    (appdata / "local-settings.json").write_text(
        json.dumps({"anthropic_api_key": key}), encoding="utf-8",
    )


def _startup() -> None:
    """The two calls main.py makes, in main.py's order."""
    app_secrets.bootstrap_environment(backend_env=BACKEND / ".env")
    local_settings.migrate_api_key_to_app_secrets()


def _report(case: str) -> None:
    live = os.environ.get("ANTHROPIC_API_KEY")
    stored = local_settings.stored_api_key()
    legacy = local_settings.legacy_stored_api_key()
    status = app_secrets.status()
    print(f"\n=== {case} ===")
    print(f"  live ANTHROPIC_API_KEY : {live}")
    print(f"  Settings pane shows    : {stored}")
    print(f"  legacy json leftover   : {legacy}")
    print(f"  status.source          : {status['source']}")
    print(f"  masked by environment  : {status['overridden_by_environment']}")
    agree = live == stored or status["overridden_by_environment"]
    print(f"  COHERENT               : {agree}")


def main() -> int:
    appdata = _fresh_appdata("keycase-legacy-")
    _write_legacy(appdata, JSON_KEY)
    _startup()
    _report("A. only the legacy json store (the upgrade path)")

    appdata = _fresh_appdata("keycase-both-")
    app_secrets._write_managed({"ANTHROPIC_API_KEY": USER_ENV_KEY})
    _write_legacy(appdata, JSON_KEY)
    _startup()
    _report("B. both stores populated (what the merge produced)")

    appdata = _fresh_appdata("keycase-shell-")
    os.environ["ANTHROPIC_API_KEY"] = SHELL_KEY
    _write_legacy(appdata, JSON_KEY)
    _startup()
    _report("C. the operator exported one on the command line")

    print(
        "\nExpected: A migrates and publishes; B keeps user.env and drops the "
        "stale entry;\nC leaves the shell value in effect and says so.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
