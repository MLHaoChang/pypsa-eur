# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
AST mutants on the lines a branch changed, run against one targeted test.

    python scripts/coord/mutants.py <src.py> <test target> [--base origin/master] [--timeout 120]

Run it on each changed engine/service module before asking a gatekeeper for an
OK; list the survivors (or why each is equivalent) in the PR body. Mutants are
made only on lines ``git diff`` marks as changed in ``<src.py>`` since the
merge base with ``--base``:

- comparison flips and boundaries (``<`` ↔ ``<=``, ``>`` ↔ ``>=``, ``==`` ↔ ``!=``, ``in`` ↔ ``not in``);
- drop one factor of ``a * b``;
- ``if c`` → ``if not c``, and ``if c`` → ``if False`` when the body only returns, raises, continues or breaks;
- ``and`` ↔ ``or``, drop one operand of a BoolOp;
- delete a statement that filters or reassigns (``x = x[...]``);
- ``return True`` ↔ ``return False``; ``raise X`` → ``pass``;
- drop one element of a dict/list literal passed to a hash, ``json.dumps`` or digest call.

Not covered: lines inside a multi-line statement (a node is mutated only when
its first line changed), decorators, non-``.py`` guards (frontend, JSON).

Each mutant runs ``pytest -x -q <target>`` in its own process group under a
timeout. A target is either ``test/`` (root) or ``pypsa-gui/backend`` tests,
never a mix. The unmutated file must pass first. The original is backed up to
``<src>.mutants.bak`` and restored after every mutant, on exit and at the next
start (a SIGKILL leaves the backup). It refuses a dirty ``<src.py>``.
"""

import argparse
import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HASH_CALL_RE = re.compile(r"hash|dumps|digest|sha\d*|md5|blake|hmac|update", re.I)
FLIP = {
    ast.Lt: [ast.LtE, ast.GtE],
    ast.LtE: [ast.Lt, ast.Gt],
    ast.Gt: [ast.GtE, ast.LtE],
    ast.GtE: [ast.Gt, ast.Lt],
    ast.Eq: [ast.NotEq],
    ast.NotEq: [ast.Eq],
    ast.In: [ast.NotIn],
    ast.NotIn: [ast.In],
    ast.Is: [ast.IsNot],
    ast.IsNot: [ast.Is],
}


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=REPO
    ).stdout


def changed_lines(src, base):
    """Line numbers of ``src`` added or changed since the merge base with ``base``."""
    mb = git("merge-base", base, "HEAD").strip()
    out = git("diff", "-U0", "--no-color", mb, "--", str(src))
    lines = set()
    for row in out.splitlines():
        m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", row)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            lines.update(range(start, start + count))
    return lines


def _dotted(node):
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _only_exits(body):
    return all(
        isinstance(s, (ast.Return, ast.Raise, ast.Continue, ast.Break)) for s in body
    )


def _reassigns_itself(node):
    if (
        not isinstance(node, ast.Assign)
        or len(node.targets) != 1
        or not isinstance(node.targets[0], ast.Name)
    ):
        return False
    name = node.targets[0].id
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node.value))


def _hash_literals(tree):
    """Dict/List/Set literals inside a hash-like call: ``(node, element_index)``."""
    seen = set()
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and HASH_CALL_RE.search(
            _dotted(call.func).rsplit(".", 1)[-1]
        ):
            for lit in ast.walk(call):
                if (
                    isinstance(lit, (ast.Dict, ast.List, ast.Set))
                    and id(lit) not in seen
                ):
                    seen.add(id(lit))
                    n = len(lit.keys if isinstance(lit, ast.Dict) else lit.elts)
                    yield from ((lit, i) for i in range(n))


def sites(tree, lines):
    """Every mutation site as ``(walk_index, kind, arg, lineno)`` on a changed line."""
    nodes = list(ast.walk(tree))
    index = {id(n): i for i, n in enumerate(nodes)}
    out = []

    def add(node, kind, arg=None, lineno=None):
        ln = lineno or getattr(node, "lineno", None)
        if ln in lines:
            out.append((index[id(node)], kind, arg, ln))

    for node in nodes:
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                for new in FLIP.get(type(op), []):
                    add(node, "cmp", (k, new))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            add(node, "drop_factor", "left")
            add(node, "drop_factor", "right")
        elif isinstance(node, ast.If):
            add(node, "negate_if")
            if _only_exits(node.body):
                add(node, "guard_false")
        elif isinstance(node, ast.BoolOp):
            add(node, "boolop_swap")
            for k in range(len(node.values)):
                add(node, "drop_operand", k)
        elif isinstance(node, ast.Assign) and _reassigns_itself(node):
            add(node, "delete_stmt")
        elif (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, bool)
        ):
            add(node, "return_bool")
        elif isinstance(node, ast.Raise):
            add(node, "raise_pass")
    for lit, i in _hash_literals(tree):
        elt = (lit.keys if isinstance(lit, ast.Dict) else lit.elts)[i]
        add(lit, "drop_element", i, lineno=getattr(elt, "lineno", None) or lit.lineno)
    return out


def _replace(tree, node, new):
    """Replace ``node`` (a statement or expression) in ``tree`` by ``new``."""
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if value is node:
                setattr(parent, field, new)
                return
            if isinstance(value, list) and any(v is node for v in value):
                value[:] = [new if v is node else v for v in value]
                return
    raise LookupError("node not found")


def apply(tree, site):
    """Mutate ``tree`` in place at ``site``; return ``(original_text, mutant_text)``."""
    nodes = list(ast.walk(tree))
    idx, kind, arg, _ = site
    node = nodes[idx]
    before = ast.unparse(node)
    if kind == "cmp":
        k, new = arg
        node.ops[k] = new()
        after = ast.unparse(node)
    elif kind == "drop_factor":
        keep = node.right if arg == "left" else node.left
        _replace(tree, node, keep)
        after = ast.unparse(keep)
    elif kind in ("negate_if", "guard_false"):
        before = f"if {ast.unparse(node.test)}:"
        node.test = (
            ast.UnaryOp(op=ast.Not(), operand=node.test)
            if kind == "negate_if"
            else ast.Constant(value=False)
        )
        after = f"if {ast.unparse(node.test)}:"
    elif kind == "boolop_swap":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        after = ast.unparse(node)
    elif kind == "drop_operand":
        del node.values[arg]
        after = ast.unparse(node.values[0] if len(node.values) == 1 else node)
        if len(node.values) == 1:
            _replace(tree, node, node.values[0])
    elif kind == "delete_stmt":
        _replace(tree, node, ast.Pass())
        after = "pass"
    elif kind == "return_bool":
        node.value = ast.Constant(value=not node.value.value)
        after = ast.unparse(node)
    elif kind == "raise_pass":
        _replace(tree, node, ast.Pass())
        after = "pass"
    elif kind == "drop_element":
        if isinstance(node, ast.Dict):
            del node.keys[arg], node.values[arg]
        else:
            del node.elts[arg]
        after = f"{before.splitlines()[0][:40]} … (element {arg} dropped)"
    else:
        raise ValueError(kind)
    return before.splitlines()[0], after.splitlines()[0]


def mutants(source, lines):
    """Yield ``(lineno, original, mutant, mutated_source)`` for each mutant of ``source``."""
    tree0 = ast.parse(source)
    for site in sites(tree0, lines):
        tree = ast.parse(source)
        try:
            orig, mut = apply(tree, site)
            text = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
        except (LookupError, ValueError):
            continue
        yield site[3], orig, mut, text


def test_python():
    for cand in (
        os.environ.get("MUTANTS_PYTHON"),
        os.environ.get("PREPUSH_PYTHON"),
        "/root/.venv-pypsa-gui/bin/python",
    ):
        if cand and Path(cand).exists():
            return cand
    return sys.executable


def run_target(target, timeout, python=None):
    """
    Run ``pytest -x -q target``. Returns ``(status, first_failed_id)``.

    ``status`` is PASS, FAIL, TIMEOUT or ERROR (collection error, usage error).
    """
    backend = REPO / "pypsa-gui" / "backend"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(backend)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="mutants-pyc-")
    cmd = [
        python or test_python(),
        "-m",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        target,
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return "TIMEOUT", ""
    finally:
        shutil.rmtree(env["PYTHONPYCACHEPREFIX"], ignore_errors=True)
    if proc.returncode == 0:
        return "PASS", ""
    if proc.returncode == 1:
        m = re.search(r"^FAILED (\S+)", out, re.M)
        return "FAIL", m.group(1) if m else "(failed, id not parsed)"
    return "ERROR", out.strip().splitlines()[-1] if out.strip() else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src")
    ap.add_argument("target")
    ap.add_argument("--base", default="origin/master")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv)

    src = Path(a.src).resolve()
    rel = src.relative_to(REPO)
    bak = src.with_name(src.name + ".mutants.bak")
    if bak.exists():  # an earlier run was killed mid-mutant
        shutil.copyfile(bak, src)
        bak.unlink()
        print(f"restored {rel} from an earlier interrupted run")
    if git("status", "--porcelain", "--", str(rel)).strip():
        print(
            f"refusing: {rel} has uncommitted changes (commit or stash first)",
            file=sys.stderr,
        )
        return 2
    lines = changed_lines(rel, a.base)
    original = src.read_text(encoding="utf-8")
    todo = list(mutants(original, lines))
    status, first = run_target(a.target, a.timeout)
    if status != "PASS":
        print(
            f"baseline {status} on the unmutated file ({first}); fix the target first",
            file=sys.stderr,
        )
        return 2
    print(f"{len(todo)} mutants on {len(lines)} changed lines of {rel}")

    survivors, timeouts, killed = [], [], 0
    shutil.copyfile(src, bak)
    old = {
        s: signal.signal(s, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        for s in (signal.SIGTERM,)
    }
    try:
        for ln, orig, mut, text in todo:
            src.write_text(text, encoding="utf-8")
            try:
                status, first = run_target(a.target, a.timeout)
            finally:
                shutil.copyfile(bak, src)
            label = f"{rel}:{ln}  {orig} → {mut}"
            if status == "FAIL":
                killed += 1
                print(f"KILLED    {label}  [{first}]")
            elif status == "PASS":
                survivors.append(label)
                print(f"SURVIVED  {label}")
            else:
                timeouts.append(label)
                print(f"{status:<9} {label}")
    finally:
        shutil.copyfile(bak, src)
        bak.unlink(missing_ok=True)
        for s, h in old.items():
            signal.signal(s, h)
    print(
        f"\nkilled {killed}, survived {len(survivors)}, timeout/error {len(timeouts)}"
    )
    if survivors:
        print("Survivors:")
        print("\n".join(survivors))
    if timeouts:
        print("TIMEOUT/ERROR:")
        print("\n".join(timeouts))
    return 1 if survivors or timeouts else 0


if __name__ == "__main__":
    sys.exit(main())
