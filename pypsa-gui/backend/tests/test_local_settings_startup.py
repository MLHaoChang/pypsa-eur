"""
The import-time chain: a key on disk becomes ANTHROPIC_API_KEY in the process.

Runs in a SUBPROCESS on purpose. conftest imports `main` once per session, so
an in-process test can never re-trigger a module-level call — and "the chain
does not run in the real process" is exactly the defect this guards.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PROBE = (
    "import os, sys; sys.path.insert(0, %r); "
    "import main; "
    "print('KEY=' + os.environ.get('ANTHROPIC_API_KEY', ''))"
)


def _run_probe(tmp_path, *, env_key: str | None, file_key: str | None) -> str:
    """Import `main` in a clean interpreter and report the resulting env var."""
    appdata = tmp_path / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    if file_key is not None:
        (appdata / "local-settings.json").write_text(
            json.dumps({"anthropic_api_key": file_key}), encoding="utf-8",
        )

    env = dict(os.environ)
    # MANDATORY isolation: all three, or the child writes to real user data.
    env["PYPSAGUI_APP_DATA_DIR"] = str(appdata)
    env["PYPSAGUI_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{(tmp_path / 'probe.db').as_posix()}"
    env.pop("ANTHROPIC_API_KEY", None)
    if env_key is not None:
        env["ANTHROPIC_API_KEY"] = env_key

    result = subprocess.run(
        [sys.executable, "-c", PROBE % str(BACKEND)],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    for line in result.stdout.splitlines():
        if line.startswith("KEY="):
            return line[len("KEY="):]
    raise AssertionError(f"probe printed no KEY= line:\n{result.stdout[-4000:]}")


def test_stored_key_is_published_on_import(tmp_path):
    assert _run_probe(tmp_path, env_key=None, file_key="sk-ant-from-the-file") == (
        "sk-ant-from-the-file"
    )


def test_environment_wins_over_the_stored_key(tmp_path):
    """A shell that exported a key must not be overridden by app-data."""
    assert _run_probe(
        tmp_path, env_key="sk-ant-from-the-shell", file_key="sk-ant-from-the-file",
    ) == "sk-ant-from-the-shell"


def test_no_stored_key_leaves_the_variable_unset(tmp_path):
    assert _run_probe(tmp_path, env_key=None, file_key=None) == ""
