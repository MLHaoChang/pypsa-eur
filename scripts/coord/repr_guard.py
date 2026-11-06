# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Repr diff guard: no new ``!r}`` in user-visible backend text.

docs/agents/review-ux.md: user-visible text carries no Python-repr quotes. A
ratchet: the ~1000 existing ``!r}`` stay; this fails on lines a branch ADDS
(vs the merge base with ``<base>``) to non-test ``pypsa-gui/backend/**/*.py``
that contain ``!r}``, unless the line sits inside a logging call
(``log.``, ``logger.``, ``logging.``, ``_log``, ``warnings.warn``) or carries
``# repr-ok: <reason>``.

    python scripts/coord/repr_guard.py [<base>]     # default origin/master
"""

import ast
import re
import subprocess
import sys

SCOPE = "pypsa-gui/backend"
LOG_NAMES = {"log", "logger", "logging"}
ESCAPE_RE = re.compile(r"#\s*repr-ok:\s*\S")


def is_test_path(path):
    parts = path.split("/")
    name = parts[-1]
    return "tests" in parts[:-1] or name.startswith("test_") or name == "conftest.py"


def added_lines(diff):
    """Yield ``(path, lineno)`` for every added line of a ``-U0`` diff."""
    path, ln = None, 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
        elif row.startswith("@@"):
            ln = int(re.search(r"\+(\d+)", row).group(1)) - 1
        elif row.startswith("+") and path:
            ln += 1
            yield path, ln


def _dotted(node):
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return node.id if isinstance(node, ast.Name) else None


def is_log_call(name):
    """``log.info``, ``self.logger.warning``, ``_log_x(...)``, ``warnings.warn``."""
    if name == "warnings.warn":
        return True
    return any(seg in LOG_NAMES or seg.startswith("_log") for seg in name.split("."))


def log_ranges(source):
    """Line ranges of calls to a logging function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name and is_log_call(name):
                out.append((node.lineno, node.end_lineno))
    return out


def check_file(path, linenos, source):
    """Return ``[(lineno, text)]`` of offending added lines in ``source``."""
    lines = source.splitlines()
    ranges = None
    bad = []
    for ln in linenos:
        text = lines[ln - 1] if ln <= len(lines) else ""
        if "!r}" not in text or ESCAPE_RE.search(text):
            continue
        if ranges is None:
            ranges = log_ranges(source) or []
        if any(a <= ln <= b for a, b in ranges):
            continue
        if re.match(
            r"\s*(?:(?:log|logger|logging|_log\w*)\.\w+\(|warnings\.warn\()", text
        ):
            continue
        bad.append((ln, text.strip()))
    return bad


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    base = argv[0] if argv else "origin/master"
    mb = git("merge-base", base, "HEAD").strip()
    diff = git("diff", "-U0", "--no-color", mb, "--", f"{SCOPE}/*.py")
    by_file = {}
    for path, ln in added_lines(diff):
        if path.endswith(".py") and not is_test_path(path):
            by_file.setdefault(path, []).append(ln)
    fails = []
    for path, linenos in sorted(by_file.items()):
        try:
            source = open(path, encoding="utf-8").read()
        except OSError:
            continue
        fails += [(path, ln, text) for ln, text in check_file(path, linenos, source)]
    for path, ln, text in fails:
        print(f"{path}:{ln}: new `!r}}` in user-visible text: {text}")
    if fails:
        print(
            "Use a plain value (quotes only where the user typed them), or `# repr-ok: <reason>` for debug text."
        )
        return 1
    print("repr guard: no new !r} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
