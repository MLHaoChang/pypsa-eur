"""
Three checks for a merge that a green test suite will not perform for you.

WHY THIS EXISTS. Between 2026-09-10 and 2026-09-12 `master` moved five times
under one long-lived branch. Each merge was resolved carefully and THREE of the
five produced a defect that NO TEST CAUGHT, because a merge can delete work
without breaking anything that remains:

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

WHAT A CLEAN RUN ACTUALLY MEANS, stated narrowly because an independent review
found the first version of this docstring overselling it by a wide margin:

    No definition either parent changed is present in the merge holding its
    exact merge-base text, no module has two top-level definitions of one name,
    and no top-level NAME or --inventory literal that a parent had is missing
    from the whole merged tree.

That is all. In particular a clean run does NOT mean:

  * "our edit survived" — if the resolution took THEIRS' version, the merged
    text equals neither base nor ours, and the lost-edit check is silent. This
    is the most common real shape and the tool cannot see it. Same for a
    partial resolution that keeps some of our terms and drops others;
  * "no constant was reverted" — module-level assignments are checked for the
    presence of the NAME, never the value. `TIMEOUT = 300` rewound to `30` is
    invisible;
  * "nothing master added was dropped" — see the `theirs` section below: since
    2026-09-12 the lost-edit check runs in both directions, but only for the
    exact-base-text shape above;
  * "nothing new was dropped" — a method ADDED by a parent inside a class and
    then dropped is caught by neither check (lost-edit needs the name to exist
    in base; the vocabulary check does not descend into classes). A TOP-LEVEL
    addition that is dropped IS caught.

Read a finding. Do not read a zero.

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
import contextlib
import functools
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile


# ── git plumbing ────────────────────────────────────────────────────────────

def _git(*args: str, cwd: str | None = None) -> str:
    """
    git, decoded permissively.

    NOT `text=True`. A single latin-1 `.py` anywhere under `--path` made
    `subprocess` raise `UnicodeDecodeError` from inside `_read`, which catches
    only `RuntimeError` — so one undecodable byte aborted the whole audit and
    exited 1, the code that means "findings". `errors="replace"` costs nothing:
    a mojibake character cannot turn a lost edit into a surviving one, because
    both sides of every comparison are mangled identically.
    """
    out = subprocess.run(["git", *args], capture_output=True, cwd=cwd)
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)}: {err}")
    return out.stdout.decode("utf-8", "replace")


@functools.lru_cache(maxsize=None)
def _read_ref(ref: str, path: str, cwd: str | None) -> str | None:
    try:
        return _git("show", f"{ref}:{path}", cwd=cwd)
    except RuntimeError:
        return None          # absent at that ref, which is a fact not an error


def _read(ref: str | None, path: str, cwd: str | None = None) -> str | None:
    """
    File content at `ref`, or from the working tree when `ref` is None.

    Git refs are cached; THE WORKING TREE IS NOT, and the distinction is not
    cosmetic. Every read is a `git show` subprocess and the checks read the
    same file at the same ref several times each — the merged tree alone is
    read by `scan_health`, `_merged_index` and both directions of
    `check_lost_edits` — so uncached this took minutes on one real merge. But a
    ref's content cannot change under us and the working tree's can: caching
    `ref is None` broke all four positive cases of the self-test at once, which
    rewrites the file between phases. That is the self-test doing its job, and
    it is the reason the two paths are separate functions rather than one
    `lru_cache` over both.
    """
    if ref is None:
        p = pathlib.Path(cwd or ".") / path
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return _read_ref(ref, path, cwd)


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

_DEF = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _segment(src: str, node: ast.AST) -> str | None:
    """
    A definition's source INCLUDING its decorators.

    `ast.get_source_segment` starts at the `def`/`class` line, so decorators are
    not part of what it returns and a dropped decorator compares equal. In a
    FastAPI backend that is not a corner case: it hides a lost
    `@requires_capability`, a changed route path or method, `@property`,
    `@staticmethod`, `@lru_cache` and `@pytest.mark.*`. Losing a capability
    guard in a merge is an incident this repository has actually had.
    """
    seg = ast.get_source_segment(src, node)
    if seg is None:
        return None
    decorators = getattr(node, "decorator_list", [])
    if not decorators:
        return seg
    parts = [ast.get_source_segment(src, d) or "" for d in decorators]
    return "\n".join(f"@{part}" for part in parts) + "\n" + seg


def _defs(src: str | None) -> dict[str, str]:
    """
    {qualified name: normalised source} for every def/class, at any depth.

    QUALIFIED, and it was not. The first version keyed by `node.name` under an
    `ast.walk`, so the last definition walked silently overwrote every earlier
    one sharing a bare name — and `ast.walk` is breadth-first, so which one won
    was not even predictable from reading the file. Measured on this repository:
    250 of 7,415 definitions across 52 files were invisible to the audit,
    including nine colliding names in `routers/results.py` (a file a merge
    really did damage) and four of the five `__init__` bodies in
    `services/solver/runtime.py`. A tool that cannot see a method is not a tool
    that can tell you the method survived.
    """
    if src is None:
        return {}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF):
                qual = f"{prefix}{child.name}"
                seg = _segment(src, child)
                if seg is not None:
                    # Normalised so reindentation or rewrapped comments are not
                    # mistaken for a change; a moved function is not a lost one.
                    out[qual] = " ".join(seg.split())
                walk(child, f"{qual}.")
            else:
                # Descend through `if`/`try`/`with` bodies so a conditionally
                # defined function keeps the qualified name a reader expects.
                walk(child, prefix)

    walk(tree, "")
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


# ── did we actually look at anything? ───────────────────────────────────────

_CONFLICT_MARKERS = ("<" * 7 + " ", "=" * 7 + "\n", ">" * 7 + " ")


def scan_health(merged, pathspec, cwd=None) -> dict:
    """
    How many files were examined, and how many could not be parsed.

    EVERY failure mode of this tool is biased toward FALSE QUIET, so the run has
    to report its own denominator. A typo in `--path`, or running from
    `pypsa-gui/` instead of the repo root, produced a confident "no findings"
    and exit 0. A file with a syntax error was skipped silently by `_defs`,
    taking a real duplicate definition with it. Neither said anything.

    Conflict markers get their own count because `--merged` defaults to the
    working tree: that makes the tool runnable mid-merge, but a file still
    holding markers does not parse, so every definition in it is reported
    "ABSENT from the merge". On a 23-file conflict that buries the real
    findings under phantoms indistinguishable from them. The tool now refuses.
    """
    files = _python_files(merged, pathspec, cwd)
    unparseable, conflicted = [], []
    for path in files:
        src = _read(merged, path, cwd)
        if src is None:
            continue
        if any(mark in src for mark in _CONFLICT_MARKERS):
            conflicted.append(path)
            continue
        try:
            ast.parse(src)
        except (SyntaxError, ValueError):
            unparseable.append(path)
    return {"files": len(files), "unparseable": unparseable,
            "conflicted": conflicted}


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
    # The union is the semantically right set — "files either side knows
    # about" — but it is currently REDUNDANT: a file present in `ours` and not
    # in `base` has an empty `_defs(base)`, so `changed` below is empty for it.
    # Noted so nobody writes a self-test case to kill a mutation that narrows
    # this to `base` alone; that mutation is equivalent, and the case would be
    # unkillable rather than missing.
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

_SELF_TEST_CASES = (
    "17 cases: lost edits (method, async, decorator, absent, second directory), "
    "both parents audited, duplicates, re-exports, imports-are-not-defs, "
    "reindentation is not a change, conflict markers, unparseable files"
)


def _write(root: str, rel: str, text: str) -> None:
    path = pathlib.Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# The fixture, as three revisions of the same small tree. It is deliberately
# awkward: two directories, a class with a method, an async function, a
# decorated function, a plain `import`, a `from ... import ... as ...`
# re-export, and TWO definitions sharing a bare name in one file. An earlier
# fixture was three flat top-level functions in one file with no import
# statement anywhere, and a sabotage matrix showed 10 of 16 subtle mutations
# passing it — including the one regression this tool has actually had
# (widening `_top_level_definitions` to count imports), which that fixture
# could not exercise because it contained nothing to import.

_BASE = {
    "pkg/m.py": (
        "import os\n"
        "from pkg.helpers import shared as _shared\n"
        "\n\n"
        "def f():\n    return 1\n"
        "\n\n"
        "def g():\n    return 'keep'\n"
        "\n\n"
        "async def a():\n    return 'async-base'\n"
        "\n\n"
        "@deco('base')\n"
        "def decorated():\n    return 'body'\n"
        "\n\n"
        "class Alpha:\n    def run(self):\n        return 'alpha-base'\n"
        "\n\n"
        "class Beta:\n    def run(self):\n        return 'beta-base'\n"
    ),
    "pkg/helpers.py": "def shared():\n    return 'shared'\n",
    "other/n.py": "def only_in_other():\n    return 'other-base'\n",
}

_OURS = dict(_BASE)
_OURS["pkg/m.py"] = (
    _BASE["pkg/m.py"]
    .replace("def f():\n    return 1", "def f():\n    return 2")
    .replace("return 'async-base'", "return 'async-OURS'")
    .replace("@deco('base')", "@deco('OURS')")
    .replace("return 'alpha-base'", "return 'alpha-OURS'")
)
_OURS["other/n.py"] = "def only_in_other():\n    return 'other-OURS'\n"
_OURS["pkg/ours_only.py"] = "def ours_only_symbol():\n    return 1\n"

_THEIRS = dict(_BASE)
_THEIRS["pkg/m.py"] = _BASE["pkg/m.py"] + "\n\ndef h():\n    return 3\n"


def self_test() -> int:
    """
    Plant each defect in a throwaway repository and assert the check finds it —
    then assert a clean merge of the same shape reports nothing.

    A check that cannot fail is worse than no check: it makes the audit a
    formality and trains the next person to skip it. But a self-test that only
    proves each check is non-EMPTY is the same formality one level up, which is
    what the first version of this was: it asserted three findings existed and
    nothing about their shape, on a fixture with no class, no decorator, no
    nesting, no async, no import and one directory. Each case below names the
    mutation it is there to kill.
    """
    failures = []

    def want(cond, msg):
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        r = tmp
        _git("init", "-q", "-b", "base", cwd=r)
        _git("config", "user.email", "t@t", cwd=r)
        _git("config", "user.name", "t", cwd=r)

        for rel, text in _BASE.items():
            _write(r, rel, text)
        _git("add", "-A", cwd=r); _git("commit", "-qm", "base", cwd=r)
        base = _git("rev-parse", "HEAD", cwd=r).strip()

        _git("checkout", "-q", "-b", "ours", cwd=r)
        for rel, text in _OURS.items():
            _write(r, rel, text)
        _git("add", "-A", cwd=r); _git("commit", "-qm", "ours", cwd=r)
        ours = _git("rev-parse", "HEAD", cwd=r).strip()

        _git("checkout", "-q", "-b", "theirs", base, cwd=r)
        for rel, text in _THEIRS.items():
            _write(r, rel, text)
        _git("add", "-A", cwd=r); _git("commit", "-qm", "theirs", cwd=r)
        theirs = _git("rev-parse", "HEAD", cwd=r).strip()

        _git("checkout", "-q", "ours", cwd=r)

        def lay(tree: dict) -> None:
            """Put a candidate merge result in the working tree."""
            for rel in set(_BASE) | set(_OURS) | set(_THEIRS) | set(tree):
                path = pathlib.Path(r) / rel
                if rel in tree:
                    _write(r, rel, tree[rel])
                elif path.exists():
                    path.unlink()

        def lost(pathspec="."):
            return check_lost_edits(base, ours, None, pathspec, r)

        def voc(pathspec="."):
            return check_vocabulary(base, ours, theirs, None, pathspec, [], r)

        # A correct merge: ours' edits kept, theirs' addition kept.
        good = dict(_OURS)
        good["pkg/m.py"] = _OURS["pkg/m.py"] + "\n\ndef h():\n    return 3\n"

        # 1. a GOOD merge reports nothing at all
        lay(good)
        gone, moved = lost()
        clean = (gone + moved + check_duplicate_definitions(None, ".", r) + voc())
        want(not clean, f"a clean merge reported findings: {clean}")

        # 2. REINDENTATION is not a change — in BOTH directions, and only the
        #    second direction can kill "drop the normalisation".
        #    (a) a reindented copy of OUR version is not a loss;
        reindented = dict(good)
        reindented["pkg/m.py"] = good["pkg/m.py"].replace(
            "def f():\n    return 2", "def f():\n        return 2")
        lay(reindented)
        want(not lost()[0] and not lost()[1],
             "reindenting our own version was reported as a lost edit")
        #    (b) a reindented copy of the BASE version IS a loss. Without the
        #        normalisation this compares unequal to base and the revert
        #        goes unreported — a false clean, which is the direction that
        #        matters.
        reverted_reindented = dict(good)
        reverted_reindented["pkg/m.py"] = good["pkg/m.py"].replace(
            "def f():\n    return 2", "def f():\n        return 1")
        lay(reverted_reindented)
        want(any("::f" in f for f in lost()[0]),
             "a REINDENTED revert to the base copy was not caught "
             "(normalisation dropped?)")

        # 3. A METHOD's edit lost, where the name collides with another class's
        #    method. Kills "only look at top level" AND "key by bare name".
        m = dict(good)
        m["pkg/m.py"] = good["pkg/m.py"].replace("return 'alpha-OURS'", "return 'alpha-base'")
        lay(m)
        want(any("Alpha.run" in f for f in lost()[0]),
             "a lost METHOD edit was not caught (nested, or bare-name collision)")

        # 4. An ASYNC function's edit lost. Kills "forget AsyncFunctionDef".
        m = dict(good)
        m["pkg/m.py"] = good["pkg/m.py"].replace("return 'async-OURS'", "return 'async-base'")
        lay(m)
        want(any("::a " in f or f.endswith("::a") or "::a —" in f for f in lost()[0]),
             "a lost ASYNC function edit was not caught")

        # 5. A DECORATOR lost, body identical. Kills "segment excludes
        #    decorator_list" — the capability-guard incident in one line.
        m = dict(good)
        m["pkg/m.py"] = good["pkg/m.py"].replace("@deco('OURS')", "@deco('base')")
        lay(m)
        want(any("decorated" in f for f in lost()[0]),
             "a lost DECORATOR was not caught (segment excludes decorators?)")

        # 6. A function ABSENT from the merge, defined nowhere else. Kills both
        #    "stop reporting absent functions" and "always downgrade to moved".
        m = dict(good)
        m["other/n.py"] = "def something_else():\n    return 0\n"
        lay(m)
        gone, moved = lost()
        want(any("only_in_other" in f and "ABSENT" in f for f in gone),
             "an absent changed function was not reported as GONE")
        want(not any("only_in_other" in f for f in moved),
             "an absent function with no other home was downgraded to 'moved'")

        # 7. A file that exists ONLY on our side, dropped. Kills "ignore files
        #    absent from base".
        m = dict(good)
        m.pop("pkg/ours_only.py")
        lay(m)
        want(any("ours_only_symbol" in f for f in voc()),
             "a symbol from an OURS-only file was not reported when dropped")

        # 8. A symbol OURS had, gone. Kills "vocabulary only audits theirs".
        want(any("on ours" in f for f in voc()),
             "check_vocabulary did not report an OURS-side loss")

        # 9. Theirs' addition dropped — the other direction.
        lay(_OURS)
        want(any("h" in f and "on theirs" in f for f in voc()),
             "check_vocabulary did not report a THEIRS-side loss")

        # 10. MASTER's edit reverted to base. Kills "only audit our side".
        theirs_edit = dict(_BASE)
        theirs_edit["pkg/helpers.py"] = "def shared():\n    return 'THEIRS'\n"
        _git("checkout", "-q", "theirs", cwd=r)
        _write(r, "pkg/helpers.py", theirs_edit["pkg/helpers.py"])
        _git("commit", "-qam", "theirs edits helpers", cwd=r)
        theirs2 = _git("rev-parse", "HEAD", cwd=r).strip()
        _git("checkout", "-q", "ours", cwd=r)
        lay(good)          # good keeps the BASE copy of helpers.py
        t_gone, _ = check_lost_edits(base, theirs2, None, ".", r)
        want(any("shared" in f for f in t_gone),
             "an edit MASTER made, reverted by the merge, was not caught")
        # …and through `_run`, so the WIRING is covered too. Calling the check
        # directly leaves `_run`'s two-direction loop untested: dropping the
        # `master` half of it passed every case above.
        cwd_before = os.getcwd()
        try:
            os.chdir(r)
            with contextlib.redirect_stdout(io.StringIO()):
                code = _run(argparse.Namespace(
                    ours=ours, theirs=theirs2, merged=None, path=".",
                    inventory=[]))
        finally:
            os.chdir(cwd_before)
        want(code == 1, "a master-side loss did not reach the report (exit "
                        f"{code}, expected 1)")

        # 11. DUPLICATE definition.
        m = dict(good)
        m["pkg/m.py"] = good["pkg/m.py"] + "\n\ndef h():\n    return 'shadow'\n"
        lay(m)
        want(check_duplicate_definitions(None, ".", r),
             "check_duplicate_definitions did NOT catch a shadowed def")

        # 12. An IMPORT must never count as a duplicate definition. This is the
        #     exact regression the tool has had, and the old fixture could not
        #     express it — it had no import statement in it.
        m = dict(good)
        m["pkg/m.py"] = "import os\nfrom os import path\n\n\ndef path():\n    return 1\n"
        lay(m)
        want(not check_duplicate_definitions(None, ".", r),
             "an import was counted as a duplicate top-level definition")

        # 13. A re-export is not a missing symbol. Kills "forget ImportFrom".
        m = dict(good)
        m["pkg/m.py"] = good["pkg/m.py"].replace(
            "def g():\n    return 'keep'", "from pkg.helpers import shared as g")
        lay(m)
        want(not any("::g" in f for f in voc()),
             "a function turned into a re-export was reported as missing")

        # 14. The SECOND directory must be scanned. Kills "skip part of the
        #     tree" — `other/` is only reachable if the walk is complete.
        m = dict(good)
        m["other/n.py"] = "def only_in_other():\n    return 'other-base'\n"
        lay(m)
        want(any("other/n.py" in f for f in lost()[0]),
             "a loss in the SECOND directory was not seen")

        # 15. A --path that matches nothing must not read as CLEAN. Asserted
        #     on the EXIT CODE, because "no findings, exit 0" from a run that
        #     examined nothing is this tool's worst failure mode — a --path
        #     typo or the wrong working directory both land here.
        health = scan_health(None, "no/such/dir", r)
        want(health["files"] == 0, "scan_health miscounted an empty pathspec")
        cwd_before = os.getcwd()
        try:
            os.chdir(r)
            with contextlib.redirect_stdout(io.StringIO()):
                code = _run(argparse.Namespace(
                    ours=ours, theirs=theirs, merged=None, path="no/such/dir",
                    inventory=[]))
        finally:
            os.chdir(cwd_before)
        want(code == 2, f"a pathspec matching nothing exited {code}, not 2")

        # 16. Conflict markers must be detected, not parsed away.
        m = dict(good)
        m["pkg/m.py"] = ("<" * 7 + " HEAD\n" + good["pkg/m.py"] + "\n" + ">" * 7 + " x\n")
        lay(m)
        want(scan_health(None, ".", r)["conflicted"],
             "a file full of conflict markers was not detected")

        # 17. A syntax error must be surfaced, not silently skipped.
        m = dict(good)
        m["pkg/m.py"] = "def broken(:\n    pass\n"
        lay(m)
        want(scan_health(None, ".", r)["unparseable"],
             "an unparseable file was not surfaced")

    for f in failures:
        print(f"SELF-TEST FAILED: {f}")
    if failures:
        return 1
    print(f"self-test passed — {_SELF_TEST_CASES}")
    return 0


# ── entry point ─────────────────────────────────────────────────────────────

def _run(args) -> int:
    """The audit proper. `main` wraps this so any escape becomes exit 2."""
    try:
        base = _git("merge-base", args.ours, args.theirs).strip()
    except RuntimeError as exc:
        print(f"could not find a merge base: {exc}")
        return 2

    for pat in args.inventory:
        try:
            re.compile(pat)
        except re.error as exc:
            print(f"--inventory {pat!r} is not a valid regex: {exc}")
            return 2

    merged_label = args.merged or "the working tree"
    print(f"base={base[:12]}  ours={args.ours}  theirs={args.theirs}  merged={merged_label}")
    print(f"path={args.path}")

    health = scan_health(args.merged, args.path)
    print(f"examined {health['files']} .py file(s) in the merged tree")
    if not health["files"]:
        # Not "clean". We looked at nothing, and saying "no findings" here is
        # the single most misleading thing this tool could print.
        print(f"\nNOTHING TO AUDIT: no .py files under {args.path!r} at "
              f"{merged_label}. Wrong --path, or wrong working directory "
              f"(run from the repo root).")
        return 2
    if health["conflicted"]:
        print(f"\nREFUSING TO RUN: {len(health['conflicted'])} file(s) still "
              f"contain conflict markers. They do not parse, so every "
              f"definition in them would be reported ABSENT from the merge. "
              f"Resolve them first:")
        for path in health["conflicted"][:20]:
            print(f"     {path}")
        return 2
    if health["unparseable"]:
        # Not fatal — but a skipped file is a blind spot, and the old version
        # never said which.
        print(f"WARNING: {len(health['unparseable'])} file(s) do not parse and "
              f"were SKIPPED by every check:")
        for path in health["unparseable"][:20]:
            print(f"     {path}")
    print()

    groups = []
    for label, ref in (("this branch", args.ours), ("master", args.theirs)):
        gone, moved = check_lost_edits(base, ref, args.merged, args.path)
        groups.append((f"edits {label} made that the merge reverted or dropped", gone))
        groups.append((f"...and the same on {label}, where the name also exists "
                       f"elsewhere (likely a move)", moved))
    groups.append(("top-level definitions shadowed by a duplicate",
                   check_duplicate_definitions(args.merged, args.path)))
    groups.append(("definitions or literals a parent had and the merge does not",
                   check_vocabulary(base, args.ours, args.theirs, args.merged,
                                    args.path, args.inventory)))
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
        print("no findings — in the NARROW sense the module docstring states. "
              "It is not a claim that nothing was lost.")
    return 1 if total else 0


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
        return _run(args)
    except Exception as exc:          # noqa: BLE001 — see below
        # Exit 2, never 1. A bad --merged ref and an unhandled crash both used
        # to escape as a traceback, and Python exits 1 for an uncaught
        # exception — which in THIS tool's contract means "findings". A caller
        # gating a commit on the exit code could not tell "the merge is dirty"
        # from "the audit never ran".
        print(f"could not run the audit: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
