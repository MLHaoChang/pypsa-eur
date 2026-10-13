"""The staleness checks hash each large input once per version of the file
(owner decision 2026-10-13).

Every investment-case status poll with a stored report calls
``owner_assets_response``, whose staleness check sha256-hashes the whole saved
``network.nc``; ``get_state`` does it up to three times. ``_file_digest``
memoises the file's sha256 in-process, keyed on its real path, ``st_mtime_ns``,
``st_size`` and ``st_ino``: a file written again (or replaced) is hashed again.
The memo is a small LRU (``_DIGEST_CACHE_MAX`` entries), keeps one entry per
path, is guarded by a lock (the MILP job thread and the request threads both
call it), and keeps nothing for a file that changed while it was read.
"""
import os
import shutil
import threading

import pytest

from services import campus_electrical_service as ce
from tests.test_campus_electrical_ic_source import run_dir, run_milp
from tests.test_campus_electrical_milp_job import (  # noqa: F401  (fixtures)
    SETTINGS, _clean_slot, hub)
from tests.test_campus_electrical_service import hub_network, user_and_db  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _empty_memo():
    ce._DIGEST_CACHE.clear()
    yield
    ce._DIGEST_CACHE.clear()


@pytest.fixture
def spy(monkeypatch):
    """Counts the real hashing by path."""
    calls = []
    real = ce._sha256_file

    def counted(path):
        calls.append(os.path.realpath(path))
        return real(path)

    monkeypatch.setattr(ce, "_sha256_file", counted)
    return calls


def write(path, data: bytes, mtime_ns=None):
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


# ── the helper ──────────────────────────────────────────────────────────────

def test_the_digest_is_the_files_sha256(tmp_path):
    f = write(tmp_path / "a.bin", b"x" * 3_000_000)
    assert ce._file_digest(f) == ce._sha256_file(f)


def test_the_same_file_twice_is_hashed_once(tmp_path, spy):
    f = write(tmp_path / "a.bin", b"abc")
    first = ce._file_digest(f)
    assert ce._file_digest(f) == first and ce._file_digest(str(f)) == first
    assert len(spy) == 1


def test_a_touched_file_is_hashed_again(tmp_path, spy):
    f = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    ce._file_digest(f)
    write(f, b"abd", mtime_ns=2_000_000_000)                       # same size, another mtime
    assert ce._file_digest(f) == ce._sha256_file(f) and len(spy) == 3


def test_a_file_of_another_size_is_hashed_again(tmp_path, spy):
    f = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    ce._file_digest(f)
    write(f, b"abcd", mtime_ns=1_000_000_000)                      # same mtime, another size
    assert ce._file_digest(f) == ce._sha256_file(f) and len(spy) == 3


def test_a_file_replaced_by_another_of_the_same_size_and_mtime_is_hashed_again(tmp_path, spy):
    """An atomic save (a new file renamed over the old one) is another inode."""
    f = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    ce._file_digest(f)
    new = write(tmp_path / "new.bin", b"xyz", mtime_ns=1_000_000_000)
    os.replace(new, f)
    assert ce._file_digest(f) == ce._sha256_file(f) and len(spy) == 3


def test_another_path_is_another_entry(tmp_path, spy):
    a = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    b = tmp_path / "b.bin"
    shutil.copy2(a, b)                                              # same bytes, same mtime
    assert ce._file_digest(a) == ce._file_digest(b)
    assert len(spy) == 2 and len(ce._DIGEST_CACHE) == 2


def test_a_symlink_shares_the_entry_of_its_target(tmp_path, spy):
    a = write(tmp_path / "a.bin", b"abc")
    link = tmp_path / "link.bin"
    link.symlink_to(a)
    assert ce._file_digest(a) == ce._file_digest(link) and len(spy) == 1


def test_a_path_keeps_one_entry_its_latest_version(tmp_path):
    f = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    ce._file_digest(f)
    write(f, b"abcd", mtime_ns=2_000_000_000)
    ce._file_digest(f)
    assert len(ce._DIGEST_CACHE) == 1


def test_the_memo_is_bounded_and_evicts_the_least_recently_used(tmp_path, spy, monkeypatch):
    monkeypatch.setattr(ce, "_DIGEST_CACHE_MAX", 2)
    a, b, c = (write(tmp_path / f"{n}.bin", n.encode()) for n in "abc")
    ce._file_digest(a)
    ce._file_digest(b)
    ce._file_digest(a)                                              # a is used again: b is the oldest
    ce._file_digest(c)
    assert len(ce._DIGEST_CACHE) == 2 and len(spy) == 3
    ce._file_digest(a)
    ce._file_digest(c)
    assert len(spy) == 3                                            # both kept
    ce._file_digest(b)
    assert len(spy) == 4                                            # b was evicted


def test_the_default_bound_is_small():
    assert 8 <= ce._DIGEST_CACHE_MAX <= 256


def test_a_file_that_changes_while_it_is_read_is_not_kept(tmp_path, monkeypatch):
    f = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    real = ce._sha256_file

    def racing(path):
        out = real(path)
        write(f, b"abcdef", mtime_ns=2_000_000_000)                 # a save lands during the read
        return out

    monkeypatch.setattr(ce, "_sha256_file", racing)
    ce._file_digest(f)
    assert len(ce._DIGEST_CACHE) == 0
    monkeypatch.setattr(ce, "_sha256_file", real)
    assert ce._file_digest(f) == real(f)


def test_concurrent_callers_get_the_right_digests_and_the_memo_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "_DIGEST_CACHE_MAX", 3)
    files = [write(tmp_path / f"{i}.bin", bytes([i]) * (1000 + i)) for i in range(6)]
    want = {str(f): ce._sha256_file(f) for f in files}
    errors, wrong = [], []

    def worker(k):
        try:
            for j in range(200):
                f = files[(k + j) % len(files)]
                if ce._file_digest(f) != want[str(f)]:
                    wrong.append(f)
        except Exception as exc:                                    # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and not wrong and len(ce._DIGEST_CACHE) <= 3


def test_every_read_and_write_of_the_memo_holds_the_lock(tmp_path, monkeypatch):
    """Deterministic, where the threads above are only likely to collide: the
    MILP job thread and the request threads share the memo."""
    unguarded = []

    class Guarded(type(ce._DIGEST_CACHE)):
        def _check(self, what):
            if not ce._DIGEST_GUARD.locked():
                unguarded.append(what)

        def get(self, *a):
            self._check("get")
            return super().get(*a)

        def __setitem__(self, k, v):
            self._check("set")
            super().__setitem__(k, v)

        def __delitem__(self, k):
            self._check("del")
            super().__delitem__(k)

        def __iter__(self):
            self._check("iter")
            return super().__iter__()

        def move_to_end(self, *a, **kw):
            self._check("move_to_end")
            return super().move_to_end(*a, **kw)

        def popitem(self, *a, **kw):
            self._check("popitem")
            return super().popitem(*a, **kw)

    monkeypatch.setattr(ce, "_DIGEST_CACHE", Guarded())
    monkeypatch.setattr(ce, "_DIGEST_CACHE_MAX", 1)
    a = write(tmp_path / "a.bin", b"abc", mtime_ns=1_000_000_000)
    b = write(tmp_path / "b.bin", b"abc")
    ce._file_digest(a)
    ce._file_digest(a)
    write(a, b"abcd", mtime_ns=2_000_000_000)
    ce._file_digest(a)
    ce._file_digest(b)
    assert len(ce._DIGEST_CACHE) == 1 and unguarded == []


# ── the staleness checks use it ─────────────────────────────────────────────

def network_path(project):
    return ce.project_registry.project_dir(project) / ce.NETWORK_FILE


def test_repeated_state_and_owner_asset_reads_hash_the_network_once(hub, spy):
    ce.run(hub, SETTINGS)
    ce._DIGEST_CACHE.clear()
    spy.clear()
    for _ in range(3):
        ce.get_state(hub)
        ce.owner_assets_response(hub)
    nc = os.path.realpath(network_path(hub))
    assert spy.count(nc) == 1


def test_the_staleness_result_is_unchanged(hub, spy):
    ce.run(hub, SETTINGS)
    d = ce.campus_dir(hub)
    assert ce._stale(hub, d) is False and ce._stale(hub, d) is False
    n = hub_network(priced=True)
    n.loads.loc["dc_load", "p_set"] = 41.0
    n.loads_t.p["dc_load"] = 41.0
    n.export_to_netcdf(str(network_path(hub)))                      # another network, saved
    assert ce._stale(hub, d) is True
    assert ce.owner_assets_response(hub)["stale"] is True
    ce.run(hub, SETTINGS)
    assert ce._stale(hub, d) is False and ce.owner_assets_response(hub)["stale"] is False


def test_the_milp_staleness_hashes_the_least_cost_files_once(hub, monkeypatch, spy):
    run_milp(hub, monkeypatch)
    spy.clear()
    d = ce.campus_dir(hub)
    assert ce._milp_stale(hub, d) is False
    first = len(spy)
    assert ce._milp_stale(hub, d) is False and ce._milp_stale(hub, d) is False
    assert len(spy) == first                                        # nothing hashed again
    inputs = {os.path.realpath(run_dir(hub) / name) for name in ce._milp_inputs()
              if (run_dir(hub) / name).is_file()}
    assert len(inputs) >= 8 and inputs <= set(spy)
    with open(run_dir(hub) / ce.cs.SELECTED_CSV, "a") as fh:      # a least-cost file changes
        fh.write("\n")
    assert ce._milp_stale(hub, d) is True
