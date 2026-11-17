"""RP-21: one fetch per cutout key when solves run concurrently.

With `PYPSA_GUI_MAX_CONCURRENT_SOLVES` > 1, two profile jobs for the same bbox
and year used to fetch the same months into the same `<key>.parts/` and both
merge into `<key>.nc`. `ensure_cutout` now holds a process-wide lock per
cutout across the month loop and the merge: the second call waits, then finds
the complete `<key>.nc`. The wait honours the caller's stop_event.
"""

from __future__ import annotations

import threading
import time

import pytest

from services.profile_gen.cutout_cache import CutoutDownloadAborted, ensure_cutout
from tests import cutout_fakes

BBOX = (13.0, 52.0, 14.0, 53.0)
YEAR = 2020


@pytest.fixture()
def cds_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    monkeypatch.setenv("CDSAPI_KEY", "test-token-abc123")
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _run(results, name, **kwargs):
    try:
        results[name] = ensure_cutout(BBOX, YEAR, **kwargs)
    except BaseException as exc:  # noqa: BLE001 — asserted by the test
        results[name] = exc


def test_two_calls_for_the_same_key_fetch_each_month_once(cds_env, monkeypatch):
    cache = cds_env / "cutouts"
    log = cds_env / "fetches.log"
    log.touch()
    monkeypatch.setenv("CUTOUT_FAKE_LOG", str(log))  # spawn children inherit it

    results: dict = {}
    threads = [
        threading.Thread(
            target=_run,
            args=(results, name),
            kwargs=dict(
                cache_dir=cache,
                downloader=cutout_fakes.log_fetch,
                fetch_in_subprocess=True,
            ),
        )
        for name in ("a", "b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(300)
    assert not any(t.is_alive() for t in threads)

    assert not isinstance(results["a"], BaseException), results["a"]
    assert not isinstance(results["b"], BaseException), results["b"]
    assert results["a"] == results["b"]
    assert results["a"].exists()
    fetched = [int(line) for line in log.read_text().split()]
    assert sorted(fetched) == list(range(1, 13))


def test_aborting_the_waiting_call_leaves_the_running_one_alone(cds_env):
    cache = cds_env / "cutouts"
    gate = threading.Event()
    first_fetch = threading.Event()
    calls: list[tuple[str, int]] = []

    def gated(bbox, year, month, target, stop_event):
        calls.append((threading.current_thread().name, month))
        first_fetch.set()
        assert gate.wait(30)
        cutout_fakes.write_month(target, year, month)

    def fetch_as(owner):
        def downloader(bbox, year, month, target, stop_event):
            threading.current_thread().name = owner
            gated(bbox, year, month, target, stop_event)
        return downloader

    results: dict = {}
    stop_b = threading.Event()
    a = threading.Thread(
        target=_run, args=(results, "a"),
        kwargs=dict(cache_dir=cache, downloader=fetch_as("a")),
    )
    a.start()
    assert first_fetch.wait(10)

    b = threading.Thread(
        target=_run, args=(results, "b"),
        kwargs=dict(cache_dir=cache, downloader=fetch_as("b"), stop_event=stop_b),
    )
    b.start()
    time.sleep(0.5)
    assert calls == [("a", 1)]  # b is waiting, not fetching
    assert b.is_alive()

    stop_b.set()
    b.join(5)
    assert not b.is_alive()
    assert isinstance(results["b"], CutoutDownloadAborted)
    assert a.is_alive() and "a" not in results  # the running call is untouched

    gate.set()
    a.join(30)
    assert not a.is_alive()
    assert results["a"].exists()
    assert [m for _, m in calls] == list(range(1, 13))
