"""
Three checks for a merge that a green test suite will not perform for you.

WHY THIS EXISTS. Between 2026-09-10 and 2026-09-12 `master` moved five times
under one long-lived branch. Each merge was resolved carefully and each produced
a defect that NO TEST CAUGHT, because a merge can delete work without breaking
anything that remains:

  * a per-function audit of 122 changed functions found FOUR outright losses —
    the tree held the merge-base copy of `with_periodized_cost_defaults`, so
    every capital-cost figure in the app read EUR 0.00. It surfaced later as 70
    failures from one root, and only because a test happened to assert a number;
  * a security control (the `tool_not_offered` capability guard) was ported into
    a conflict block that the NEXT hunk's resolution then replaced. Every seam
    test still passed; the guard was simply gone;
  * a hunk-by-hunk resolution left `bulk_update` spliced against auto-merged
    regions using the old variable names — 9 undefined names — and two modules
    ended up with byte-identical DUPLICATE top-level definitions, one shadowing
    the other.

The common shape: the merged tree is internally consistent and passes, while
something that existed in a parent is silently absent. Tests assert what the
code does, not that it still does everything it used to.

WHAT THIS IS NOT. It is not a substitute for running the suite, and it proves
nothing about behaviour. It answers one question — "did anything from either
parent quietly not make it?" — and answers it structurally.

USAGE

    # after resolving, before committing:
    python pypsa-gui/backend/tools/merge_audit.py --theirs origin/master

    # explicit refs, e.g. auditing a merge that is already committed:
    python pypsa-gui/backend/tools/merge_audit.py \
        --ours <merge>^1 --theirs <merge>^2 --merged <merge>

    # prove the checks can actually fail:
    python pypsa-gui/backend/tools/merge_audit.py --self-test

`--ours` defaults to HEAD, `--merged` to the WORKING TREE (so it runs mid-merge,
which is the point), and the base is `git merge-base ours theirs`.

EXIT CODES. 0 clean, 1 findings, 2 could not run. Findings are not automatically
defects: a function you deliberately reverted to the base version is a finding
too. The check tells you where to look, not what to think.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import re
import subprocess
import sys
import tempfile


# ── git plumbing ────────────────────────────────────────────────────────────

def _git(*args: str, cwd: str | None = None) -> str:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _read(ref: str | None, path: str, cwd: str | None = None) -> str | None:
    """File content at `ref`, or from the working tree when `ref` is None."""
    if ref is None:
        p = pathlib.Path(cwd or ".") / path
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    try:
        return _git("show", f"{ref}:{path}", cwd=cwd)
    except RuntimeError:
        return None          # absent at that ref, which is a fact not an error


def _python_files(ref: str | None, pathspec: str, cwd: str | None = None) -> list[str]:
    if ref is None:
        root = pathlib.Path(cwd or ".")
        return sorted(
            str(p.relative_to(root))
            for p in (root / pathspec).rglob("*.py")
            if ".pixi" not in p.parts and "__pycache__" not in p.parts
        )
    listing = _git("ls-tree", "-r", "--name-only", ref, "--", pathspec, cwd=cwd)
    return [
        f for f in listing.split("\n")
        if f.endswith(".py") and ".pixi/" not in f and "__pycache__" not in f
    ]


# ── parsing ─────────────────────────────────────────────────────────────────

def _defs(src: str | None) -> dict[str, str]:
    """{qualified name: normalised source} for every def/class, at any depth."""
    if src is None:
        return {}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        seg = ast.get_source_segment(src, node)
        if seg is not None:
            # Normalised so reindentation or rewrapped comments are not
            # mistaken for a change; a moved function is not a lost one.
            out[node.name] = " ".join(seg.split())
    return out


def _top_level_names(src: str | None) -> list[str]:
    """
    Every name a module BINDS at the top level — including by import.

    Counting only `def`/`class` was wrong and measurably so: the decomposition
    turned six `chat_service` functions into re-exports
    (`redact_secrets_in_str as _redact_secrets_in_str`), and the first version
    of this reported all six as "absent from the merge" when the symbol was
    right there. A caller cannot tell a def from an alias, so neither should
    this.
    """
    if src is None:
        return []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    names: list[str] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(n.name)
        elif isinstance(n, ast.ImportFrom):
            # `from x import y` and `... as z` — this codebase re-exports that
            # way, so those names ARE part of the module's surface.
            names.extend(a.asname or a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.Import):
            # Plain `import itertools` is a DEPENDENCY, not a symbol the module
            # offers. Counting it reported `itertools` and `_re` as "absent
            # from the merge" when an import simply stopped being needed.
            pass
        elif isinstance(n, ast.Assign):
            names.extend(t.id for t in n.targets if isinstance(t, ast.Name))
    return names


# ── the three checks ────────────────────────────────────────────────────────

def _merged_index(merged, pathspec, cwd=None) -> dict[str, set[str]]:
    """{definition name: files defining it} across the whole merged tree."""
    index: dict[str, set[str]] = collections.defaultdict(set)
    for path in _python_files(merged, pathspec, cwd):
        for name in _defs(_read(merged, path, cwd)):
            index[name].add(path)
    return index


def check_lost_edits(base, ours, merged, pathspec, cwd=None):
    """
    A function OURS changed that the merge left holding the BASE copy.

    Returns (gone, moved). The split matters in THIS repository, which has been
    decomposed three times: when a decomposition lifts a body out of a router
    into a service, the old location legitimately reverts to something base-like
    and the edit lives on elsewhere. Reporting that as a loss was the first
    version's largest source of noise — five of its fifteen findings on one real
    merge, all relocations. A name still defined somewhere else in the merged
    tree is therefore reported separately, as a thing to confirm rather than a
    thing that is wrong.
    """
    gone, moved = [], []
    index = _merged_index(merged, pathspec, cwd)
    files = set(_python_files(ours, pathspec, cwd)) | set(
        _python_files(base, pathspec, cwd)
    )
    for path in sorted(files):
        b, o = _defs(_read(base, path, cwd)), _defs(_read(ours, path, cwd))
        changed = {k for k in o if k in b and b[k] != o[k]}
        if not changed:
            continue
        m = _defs(_read(merged, path, cwd))
        for name in sorted(changed):
            elsewhere = sorted(index.get(name, set()) - {path})
            if name not in m:
                if elsewhere:
                    moved.append(
                        f"{path}::{name} — not here any more; also defined in "
                        f"{', '.join(elsewhere)}. Confirm our edit moved with it."
                    )
                else:
                    gone.append(
                        f"{path}::{name} — changed on our side, ABSENT from the merge"
                    )
            elif m[name] == b[name]:
                if elsewhere:
                    moved.append(
                        f"{path}::{name} — reverted to the base copy HERE, but also "
                        f"defined in {', '.join(elsewhere)}. Likely relocated; confirm."
                    )
                else:
                    gone.append(
                        f"{path}::{name} — merge holds the MERGE-BASE copy; our edit is gone"
                    )
    return gone, moved


def _top_level_definitions(src: str | None) -> list[str]:
    """
    Only `def` and `class` at module level.

    DELIBERATELY NARROWER than `_top_level_names`, and the two must not be
    merged back together. Widening this one to include imports took the
    duplicate check from 0 findings to 14 on a real merge, every one of them
    noise: importing a name twice, or importing a name a module also defines,
    is ordinary and harmless. A second `def` of the same name is not — it
    silently replaces the first.
    """
    if src is None:
        return []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    return [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def check_duplicate_definitions(merged, pathspec, cwd=None) -> list[str]:
    """Two top-level definitions of one name in one module: one shadows the other."""
    findings = []
    for path in _python_files(merged, pathspec, cwd):
        counts = collections.Counter(_top_level_definitions(_read(merged, path, cwd)))
        for name, n in sorted(counts.items()):
            if n > 1:
                findings.append(
                    f"{path}::{name} — defined {n}x at top level; only the last one exists"
                )
    return findings


def check_vocabulary(base, ours, theirs, merged, pathspec, patterns, cwd=None) -> list[str]:
    """
    A literal either parent had, that the merge does not.

    Top-level definition names always; plus any regex given with --inventory,
    which is how a domain vocabulary gets covered (`error_kind` values, SSE
    frame names — both of which a merge dropped silently in practice).
    """
    findings = []

    def inventory(ref):
        names, lits = set(), set()
        for path in _python_files(ref, pathspec, cwd):
            src = _read(ref, path, cwd)
            names |= {f"{path}::{n}" for n in _top_level_names(src)}
            if src:
                for pat in patterns:
                    lits |= {f"{pat} -> {m}" for m in re.findall(pat, src)}
        return names, lits

    m_names, m_lits = inventory(merged)
    # Bare names anywhere in the merged tree, so a symbol that moved between
    # modules is not reported as one that disappeared — the same relocation
    # allowance `check_lost_edits` makes, for the same reason.
    m_bare = {n.partition("::")[2] for n in m_names}
    for label, ref in (("ours", ours), ("theirs", theirs)):
        r_names, r_lits = inventory(ref)
        for gone in sorted(r_names - m_names):
            if gone.partition("::")[2] in m_bare:
                continue          # defined elsewhere now
            findings.append(f"{gone} — defined on {label}, absent from the merge")
        for gone in sorted(r_lits - m_lits):
            findings.append(f"{gone} — present on {label}, absent from the merge")
    return findings


# ── self-test: every check must be able to FAIL ─────────────────────────────

_SELF_TEST_CASES = "lost edit, duplicate definition, dropped vocabulary"


def self_test() -> int:
    """
    Plant each defect in a throwaway repository and assert the check finds it —
    then assert a clean merge of the same shape reports nothing.

    A check that cannot fail is worse than no check: it makes the audit a
    formality and trains the next person to skip it.
    """
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        r = tmp
        _git("init", "-q", "-b", "base", cwd=r)
        _git("config", "user.email", "t@t", cwd=r)
        _git("config", "user.name", "t", cwd=r)
        pkg = pathlib.Path(r) / "pkg"
        pkg.mkdir()

        (pkg / "m.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 'keep'\n")
        _git("add", "-A", cwd=r); _git("commit", "-qm", "base", cwd=r)
        base = _git("rev-parse", "HEAD", cwd=r).strip()

        # ours: change f()
        _git("checkout", "-q", "-b", "ours", cwd=r)
        (pkg / "m.py").write_text("def f():\n    return 2\n\n\ndef g():\n    return 'keep'\n")
        _git("commit", "-qam", "ours", cwd=r)
        ours = _git("rev-parse", "HEAD", cwd=r).strip()

        # theirs: add h()
        _git("checkout", "-q", "-b", "theirs", base, cwd=r)
        (pkg / "m.py").write_text(
            "def f():\n    return 1\n\n\ndef g():\n    return 'keep'\n\n\ndef h():\n    return 3\n"
        )
        _git("commit", "-qam", "theirs", cwd=r)
        theirs = _git("rev-parse", "HEAD", cwd=r).strip()

        def run(content, checks):
            (pkg / "m.py").write_text(content)
            return checks()

        # 1. a GOOD merge reports nothing
        good = ("def f():\n    return 2\n\n\ndef g():\n    return 'keep'\n"
                "\n\ndef h():\n    return 3\n")
        run(good, lambda: None)
        lost_gone, lost_moved = check_lost_edits(base, ours, None, "pkg", r)
        clean = (
            lost_gone + lost_moved
            + check_duplicate_definitions(None, "pkg", r)
            + check_vocabulary(base, ours, theirs, None, "pkg", [], r)
        )
        if clean:
            failures.append(f"a clean merge reported findings: {clean}")

        # 2. LOST EDIT — merge holds the base copy of f()
        run("def f():\n    return 1\n\n\ndef g():\n    return 'keep'\n\n\ndef h():\n    return 3\n",
            lambda: None)
        if not any("our edit is gone" in f for f in
                   check_lost_edits(base, ours, None, "pkg", r)[0]):
            failures.append("check_lost_edits did NOT catch a reverted function")

        # 3. DUPLICATE DEFINITION
        run(good + "\n\ndef h():\n    return 'shadow'\n", lambda: None)
        if not check_duplicate_definitions(None, "pkg", r):
            failures.append("check_duplicate_definitions did NOT catch a shadowed def")

        # 4. DROPPED VOCABULARY — theirs' h() missing entirely
        run("def f():\n    return 2\n\n\ndef g():\n    return 'keep'\n", lambda: None)
        if not any("absent from the merge" in f for f in
                   check_vocabulary(base, ours, theirs, None, "pkg", [], r)):
            failures.append("check_vocabulary did NOT catch a dropped definition")

        # 5. DROPPED LITERAL — the --inventory path
        run("def f():\n    return 2\n\n\ndef g():\n    return 'dropped'\n\n\ndef h():\n    return 3\n",
            lambda: None)
        if not any("keep" in f for f in
                   check_vocabulary(base, ours, theirs, None, "pkg", [r"'(\w+)'"], r)):
            failures.append("check_vocabulary did NOT catch a dropped literal")

    for f in failures:
        print(f"SELF-TEST FAILED: {f}")
    if failures:
        return 1
    print(f"self-test passed — each check demonstrably fires ({_SELF_TEST_CASES})")
    return 0


# ── entry point ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ours", default="HEAD")
    ap.add_argument("--theirs")
    ap.add_argument("--merged", default=None,
                    help="default: the working tree, so this runs mid-merge")
    ap.add_argument("--path", default="pypsa-gui/backend")
    ap.add_argument("--inventory", action="append", default=[],
                    help="regex whose matches must survive; repeatable")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.theirs:
        ap.error("--theirs is required (or use --self-test)")

    try:
        base = _git("merge-base", args.ours, args.theirs).strip()
    except RuntimeError as exc:
        print(f"could not find a merge base: {exc}")
        return 2

    merged_label = args.merged or "the working tree"
    print(f"base={base[:12]}  ours={args.ours}  theirs={args.theirs}  merged={merged_label}")
    print(f"path={args.path}\n")

    lost_gone, lost_moved = check_lost_edits(
        base, args.ours, args.merged, args.path)
    groups = [
        ("edits this branch made that the merge reverted or dropped", lost_gone),
        ("...and the same, where the name also exists elsewhere (likely a move)",
         lost_moved),
        ("top-level definitions shadowed by a duplicate",
         check_duplicate_definitions(args.merged, args.path)),
        ("definitions or literals a parent had and the merge does not",
         check_vocabulary(base, args.ours, args.theirs, args.merged,
                          args.path, args.inventory)),
    ]
    total = 0
    for title, findings in groups:
        print(f"── {title}: {len(findings)}")
        for f in findings:
            print(f"     {f}")
        total += len(findings)
    print()
    if total:
        print(f"{total} finding(s). Each is a PLACE TO LOOK, not a verdict — a "
              f"function you deliberately reverted is a finding too.")
    else:
        print("no findings.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
