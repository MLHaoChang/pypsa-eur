# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""AST mutants of ``scripts/coord/mutants.py`` on a tiny fixture module."""

import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "coord"))

import mutants  # noqa: E402

SRC = """\
import hashlib, json

def f(x, w, items):
    if x < 0:
        return False
    y = x * w + 1
    items = [i for i in items if i]
    ok = x > 1 and w > 1
    key = hashlib.sha256(json.dumps({"a": x, "period": w}).encode()).hexdigest()
    if x in items:
        raise ValueError("no")
    return True
"""


def kinds(lines):
    return sorted({(s[1], s[3]) for s in mutants.sites(ast.parse(SRC), lines)})


def test_every_class_found_on_changed_lines_only():
    found = {k for k, _ in kinds(set(range(1, 13)))}
    assert found == {
        "cmp",
        "negate_if",
        "guard_false",
        "drop_factor",
        "delete_stmt",
        "boolop_swap",
        "drop_operand",
        "return_bool",
        "raise_pass",
        "drop_element",
    }
    assert {ln for _, ln in kinds({6})} == {6}
    assert kinds({1, 2, 3}) == []


def test_apply_text_and_equivalent_zero_term_is_not_a_class():
    out = {(m[0], m[1], m[2]) for m in mutants.mutants(SRC, {4, 5, 6})}
    assert (4, "if x < 0:", "if not x < 0:") in out
    assert any(o == "x * w" and m == "x" for _, o, m in out)
    assert not any("0.0" in m for _, _, m in out)


def test_guard_removal_and_literal_element_drop():
    out = {(ln, o, m) for ln, o, m, _ in mutants.mutants(SRC, {4, 9})}
    assert (4, "if x < 0:", "if False:") in out
    assert any(ln == 9 and "dropped" in m for ln, _, m in out)


def test_mutated_source_is_valid_python():
    for _, _, _, text in mutants.mutants(SRC, set(range(1, 13))):
        ast.parse(text)


def _repo(tmp_path):
    def git(*a):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    return git


def test_end_to_end_survivor_and_restore(tmp_path, monkeypatch):
    git = _repo(tmp_path)
    git("init", "-q", "-b", "master")
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feat")
    body = "def f(x):\n    if x > 1:\n        return x * 2\n    return x\n"
    (tmp_path / "m.py").write_text(body)
    (tmp_path / "test_m.py").write_text(
        "from m import f\n\ndef test_f():\n    assert f(5) == 10\n"
    )
    git("add", ".")
    git("commit", "-qm", "change")
    monkeypatch.setattr(mutants, "REPO", tmp_path)
    monkeypatch.setenv("MUTANTS_PYTHON", sys.executable)
    monkeypatch.setattr(
        mutants, "run_target", lambda t, to, python=None: _run(tmp_path, t, to)
    )
    rc = mutants.main([str(tmp_path / "m.py"), "test_m.py", "--base", "master"])
    assert rc == 1  # f(5) never hits x > 1 vs x >= 1: the boundary mutant survives
    assert (tmp_path / "m.py").read_text() == body
    assert not (tmp_path / "m.py.mutants.bak").exists()


def _run(cwd, target, timeout):
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", target],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    import re

    if p.returncode == 0:
        return "PASS", ""
    m = re.search(r"^FAILED (\S+)", p.stdout, re.M)
    return ("FAIL", m.group(1)) if p.returncode == 1 else ("ERROR", "")


def test_refuses_dirty_source(tmp_path, monkeypatch, capsys):
    git = _repo(tmp_path)
    git("init", "-q", "-b", "master")
    (tmp_path / "m.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    (tmp_path / "m.py").write_text("x = 2\n")
    monkeypatch.setattr(mutants, "REPO", tmp_path)
    assert mutants.main([str(tmp_path / "m.py"), "t.py", "--base", "master"]) == 2
    assert "uncommitted" in capsys.readouterr().err


def test_interrupted_backup_is_restored_at_next_start(tmp_path, monkeypatch, capsys):
    git = _repo(tmp_path)
    git("init", "-q", "-b", "master")
    (tmp_path / "m.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    (tmp_path / "m.py").write_text("x = 999  # leftover mutant\n")
    (tmp_path / "m.py.mutants.bak").write_text("x = 1\n")
    monkeypatch.setattr(mutants, "REPO", tmp_path)
    monkeypatch.setattr(mutants, "run_target", lambda *a, **k: ("FAIL", "t::x"))
    assert (
        mutants.main([str(tmp_path / "m.py"), "t.py", "--base", "master"]) == 2
    )  # baseline fails: stops cleanly
    assert (tmp_path / "m.py").read_text() == "x = 1\n"
    assert "restored" in capsys.readouterr().out


def test_timeout_kills_the_process_group(tmp_path):
    t = tmp_path / "test_hang.py"
    t.write_text("import time\n\ndef test_h():\n    time.sleep(60)\n")
    mutants.REPO, old = tmp_path, mutants.REPO
    try:
        status, _ = mutants.run_target(str(t), 3, python=sys.executable)
    finally:
        mutants.REPO = old
    assert status == "TIMEOUT"
