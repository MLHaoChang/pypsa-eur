# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""AA-15: enqueue_unique refuses (opt-in) a differing solver config."""
from __future__ import annotations

import pytest

from services import solve_queue as sq


@pytest.fixture
def queue(monkeypatch):
    q = sq.SolveQueue()
    monkeypatch.setattr(q, "_ensure_dispatcher_locked", lambda: None)
    from services import solve_job_store
    monkeypatch.setattr(solve_job_store, "record_enqueued", lambda *a, **k: None)
    return q


def test_differing_config_refused(queue):
    job, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    assert created
    with pytest.raises(sq.SolveConfigConflict) as ei:
        queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":2}',
                             refuse_config_conflict=True)
    assert ei.value.existing is job
    assert len(queue.list_jobs()) == 1


def test_identical_config_stays_idempotent(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    again, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}',
                                          refuse_config_conflict=True)
    assert again is job and not created


def test_default_and_missing_config_keep_old_behaviour(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    same, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":2}')
    assert same is job and not created
    same, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json=None,
                                         refuse_config_conflict=True)
    assert same is job and not created
