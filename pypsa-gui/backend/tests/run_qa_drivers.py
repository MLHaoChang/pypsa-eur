"""
Run every standalone ``qa_*.py`` driver and fail if any of them does.

Why this is not just `pytest`
-----------------------------
``pytest.ini`` sets ``python_files = test_*.py`` on purpose — the ``qa_*.py``
files are hand-rolled PASS/FAIL scripts ending in ``sys.exit(1 if FAIL else 0)``,
not pytest functions. That decision stands. Its consequence, until this file
existed, was that **nothing ran them**: not the suite, and not CI. They were
last exercised whenever someone last typed the command by hand, and by
2026-09-05 five of the nineteen were broken and several of the "passing" ones
were exiting zero while testing almost nothing
(``docs/superpowers/findings/2026-09-05-qa-driver-rot.md``).

This runner closes that hole without reversing the ``pytest.ini`` decision: the
drivers stay scripts, and a script runs them.

Skipping is explicit, and checked
---------------------------------
``_EXCLUDED`` below is the only way a driver escapes this runner, every entry
carries the reason, and `main()` fails if a listed name does not exist. That
last part is the point: a silent exclusion is how the original rot survived, so
a stale skip list has to break the build rather than quietly shrink the set.

Usage::

    pixi run gui-qa-drivers          # from the repo root
    python tests/run_qa_drivers.py   # from pypsa-gui/backend

Exit code is 0 only when every driver run exits 0.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

_TESTS = pathlib.Path(__file__).resolve().parent
_BACKEND = _TESTS.parent

# name -> why it is not run here. Nothing else is skipped.
_EXCLUDED: dict[str, str] = {
    "qa_support.py": (
        "a library, not a driver — the sandbox + signed-in client the others import"
    ),
    "qa_phase4_compare.py": (
        "needs a backend on 127.0.0.1:8000 holding two SOLVED scenario projects, "
        "and its central check is a concurrency smoke test that only means "
        "something against real uvicorn. Run it by hand against a live server; "
        "it reports its own precondition in one line"
    ),
}


def main() -> int:
    drivers = sorted(p for p in _TESTS.glob("qa_*.py"))
    missing = [name for name in _EXCLUDED if not (_TESTS / name).is_file()]
    if missing:
        print(
            "[ERROR] _EXCLUDED names a file that does not exist: "
            + ", ".join(sorted(missing))
            + "\n        Fix the list — a stale exclusion silently shrinks the set "
            "this runner covers."
        )
        return 1

    print(f"Running {len(drivers) - len(_EXCLUDED)} qa_*.py drivers "
          f"from {_BACKEND}\n")
    for name, reason in sorted(_EXCLUDED.items()):
        print(f"  [SKIP] {name} — {reason}")
    print()

    failed: list[str] = []
    for path in drivers:
        if path.name in _EXCLUDED:
            continue
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=_BACKEND, capture_output=True, text=True,
        )
        elapsed = time.monotonic() - started
        ok = proc.returncode == 0
        print(f"  [{'PASS' if ok else 'FAIL'}] {path.stem}  ({elapsed:.0f}s)")
        if not ok:
            failed.append(path.stem)
            # Only on failure, and only the tail — a driver prints a line per
            # assertion, and nineteen full transcripts would bury the summary.
            print(f"        exit={proc.returncode}")
            for line in (proc.stdout + proc.stderr).splitlines()[-40:]:
                print(f"        | {line}")

    print()
    if failed:
        print(f"FAILED: {len(failed)} driver(s) — {', '.join(failed)}")
        return 1
    print(f"All {len(drivers) - len(_EXCLUDED)} drivers passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
