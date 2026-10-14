"""
Split the suite across CI runners: `pytest --shard-id=K --num-shards=N`.

CI runs the backend suite as N parallel jobs (".github/workflows/test.yaml",
"GUI backend shard K+1/N"). Each job collects the whole suite, then keeps only
its own share; the others are reported as deselected.

Whole files are assigned, never single tests, so a module's fixtures and
module-level state stay in one process. Files are packed greedily by test
count — largest first, each into the shard with the fewest tests so far, ties
broken by path and shard index — so the result depends only on the set of
collected nodeids, not on their order. Every shard computes the same table, and
each file lands in exactly one shard (checked by tests/test_shard_plugin.py).

Matrix jobs rather than pytest-xdist: the suite shares process state (the
context registry, ports, the app-data directory) that parallel workers in one
job would trample.

Loaded by `pypsa-gui/backend/conftest.py`. Without `--num-shards` it does
nothing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import pytest


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def assign(nodeids: Iterable[str], num_shards: int) -> dict[str, int]:
    """Map each test file to a shard index in `range(num_shards)`."""
    if num_shards < 1:
        raise ValueError(f"--num-shards must be at least 1, got {num_shards}")
    counts = Counter(_file_of(n) for n in nodeids)
    loads = [0] * num_shards
    table: dict[str, int] = {}
    for path, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        k = min(range(num_shards), key=lambda i: (loads[i], i))
        table[path] = k
        loads[k] += n
    return table


def select(nodeids: list[str], shard_id: int, num_shards: int) -> list[str]:
    """The nodeids shard `shard_id` runs, in their original order."""
    if not 0 <= shard_id < max(num_shards, 1):
        raise ValueError(f"--shard-id must be in [0, {num_shards}), got {shard_id}")
    table = assign(nodeids, num_shards)
    return [n for n in nodeids if table[_file_of(n)] == shard_id]


def pytest_addoption(parser):
    group = parser.getgroup("shard", "split the suite across CI jobs")
    group.addoption(
        "--num-shards",
        type=int,
        default=None,
        help="split the collected tests into this many shards (by file)",
    )
    group.addoption(
        "--shard-id",
        type=int,
        default=0,
        help="0-based index of the shard to run (with --num-shards)",
    )


def pytest_configure(config):
    num = config.getoption("num_shards")
    if num is not None:
        shard = config.getoption("shard_id")
        if not 0 <= shard < num:
            raise pytest.UsageError(f"--shard-id must be in [0, {num}), got {shard}")


def pytest_collection_modifyitems(config, items):
    num = config.getoption("num_shards")
    if num is None:
        return
    shard = config.getoption("shard_id")
    keep = set(select([item.nodeid for item in items], shard, num))
    selected = [item for item in items if item.nodeid in keep]
    deselected = [item for item in items if item.nodeid not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_report_header(config):
    num = config.getoption("num_shards")
    if num is not None:
        return f"shard: {config.getoption('shard_id')} of {num} (0-based)"
    return None
