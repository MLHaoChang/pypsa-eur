"""
The `--shard-id` / `--num-shards` split that CI's "GUI backend shard i/N" jobs
run (see `tests/_shard.py`).

The property that matters: over all shards, every collected test runs exactly
once. A test that lands in no shard would read as green while never running;
one that lands in two wastes a runner. Both are checked twice — on the pure
assignment, and end to end through a real pytest run of a throwaway suite.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from tests import _shard

_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _files(counts: dict[str, int]) -> list[str]:
    return [f"{path}::test_{i}" for path, n in counts.items() for i in range(n)]


def test_every_nodeid_lands_in_exactly_one_shard():
    nodeids = _files(
        {f"tests/test_{c}.py": (ord(c) * 7) % 23 + 1 for c in "abcdefghijklmnop"}
    )
    shards = [_shard.select(nodeids, k, 4) for k in range(4)]
    union = [n for s in shards for n in s]
    assert sorted(union) == sorted(nodeids)
    assert len(union) == len(set(union))


def test_a_file_never_straddles_two_shards():
    nodeids = _files({"tests/test_a.py": 5, "tests/test_b.py": 3, "tests/test_c.py": 9})
    shard_of = {n: k for k in range(3) for n in _shard.select(nodeids, k, 3)}
    for f in ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py"):
        assert len({k for n, k in shard_of.items() if n.startswith(f + "::")}) == 1


def test_selection_keeps_collection_order():
    nodeids = _files({"tests/test_b.py": 2, "tests/test_a.py": 2, "tests/test_c.py": 2})
    for k in range(2):
        chosen = _shard.select(nodeids, k, 2)
        assert chosen == [n for n in nodeids if n in chosen]


def test_split_is_balanced_by_test_count():
    # One heavy file and many light ones: greedy packing puts the heavy file
    # alone and spreads the rest, where a hash split could stack them.
    counts = {"tests/test_big.py": 40} | {
        f"tests/test_s{i:02d}.py": 4 for i in range(30)
    }
    sizes = [len(_shard.select(_files(counts), k, 4)) for k in range(4)]
    assert sum(sizes) == 160
    assert max(sizes) - min(sizes) <= 4


def test_split_does_not_depend_on_collection_order():
    nodeids = _files({f"tests/test_{c}.py": 3 + (ord(c) % 5) for c in "qwertyuiop"})
    forward = [set(_shard.select(nodeids, k, 3)) for k in range(3)]
    backward = [set(_shard.select(list(reversed(nodeids)), k, 3)) for k in range(3)]
    assert forward == backward


@pytest.mark.parametrize("shard_id, num_shards", [(-1, 4), (4, 4), (0, 0)])
def test_out_of_range_shard_is_refused(shard_id, num_shards):
    with pytest.raises(ValueError):
        _shard.select(["tests/test_a.py::test_x"], shard_id, num_shards)


def _collect(root: pathlib.Path, *args: str) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests._shard",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(_BACKEND)},
    )
    assert proc.returncode in (0, 5), proc.stdout + proc.stderr  # 5: shard got nothing
    return [line for line in proc.stdout.splitlines() if "::" in line]


def test_shards_partition_a_real_collection(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    for i in range(7):
        body = "".join(f"def test_{j}():\n    pass\n" for j in range(i + 1))
        (tmp_path / f"test_m{i}.py").write_text(body)

    full = _collect(tmp_path)
    assert len(full) == 28
    shards = [_collect(tmp_path, f"--shard-id={k}", "--num-shards=3") for k in range(3)]
    union = [n for s in shards for n in s]
    assert sorted(union) == sorted(full)
    assert len(union) == len(set(union))
    assert all(shards)


def test_no_shard_options_runs_everything(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_x.py").write_text(
        "def test_a():\n    pass\ndef test_b():\n    pass\n"
    )
    assert len(_collect(tmp_path)) == 2


def test_ci_keeps_one_check_named_gui_backend():
    """
    Sessions and branch protection look for the exact check "GUI backend". With
    the suite sharded, that name belongs to the aggregate job, which must wait
    on every shard and on the QA job, or it could go green ahead of them.

    Text, not YAML, for the reason given in test_qa_driver_coverage.py.
    """
    text = (_BACKEND.parent.parent / ".github" / "workflows" / "test.yaml").read_text()
    job = text.split("\n  gui-backend:\n", 1)
    assert len(job) == 2, "the aggregate `gui-backend` job is gone from test.yaml"
    body = job[1].split("\n\n  ", 1)[0]
    assert "    name: GUI backend\n" in body
    assert "needs: [gui-backend-changes, gui-backend-tests, gui-backend-qa]" in body
    assert text.count("name: GUI backend\n") == 1
    assert "--num-shards=4" in text
