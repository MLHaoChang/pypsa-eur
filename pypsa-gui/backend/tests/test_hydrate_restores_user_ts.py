"""
A context hydrated from disk must hold that project's own `user_ts.json`.

WHAT THIS PINS. `_hydrate_context_from_disk` deliberately did not restore the
sidecar, and its docstring said why: the user time-series store was a PROCESS
GLOBAL belonging to the foreground, so restoring a background project's profiles
into it would clobber whatever the foreground had. That reason is gone — the
store is per-`ProjectContext` and `_restore_user_ts` takes a `store=` — and the
docstring's own parenthesis ("when per-ctx `_user_ts` lands in a later phase this
can reapply safely") named this as the follow-up.

WHY IT IS NOT COSMETIC. The store can hold MORE than the network does. A saved
`network.nc` carries each `_t` table at exactly `n.snapshots`, while
`user_ts.json` carries the uploaded series at its own length — the two diverge
the moment snapshots are narrowed, which is precisely what
`POST /network/snapshots/sample_weeks` does (upload 8760 hours, sample to a few
representative weeks; the store keeps the full year, which is what
`_annual_hourly_reference` later reads).

So a context hydrated with an EMPTY store does not merely "fall back to the
netCDF" harmlessly: the next save runs `_backup_network_ts_to_user_ts`, ingests
the NARROW `_t` columns into that empty store, and serialises those over the
wider series on disk. Opening a project and saving it destroyed data the user
never touched. That is what the third test drives.

Written before the fix and proven red against it. OPEN-ITEMS 13; background in
`docs/superpowers/findings/2026-09-12-user-ts-is-a-process-global-shared-across-tenants.md`.
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa
import pytest

from routers.projects import _hydrate_context_from_disk, _save_context
from services.project_context import ProjectContext


# The network is saved with FOUR snapshots; the uploaded series is EIGHT hours
# long. That gap is the whole point — it is what `_t` cannot represent.
NARROW = pd.date_range("2025-01-01", periods=4, freq="h")
WIDE = pd.date_range("2025-01-01", periods=8, freq="h")
KEY = ("loads", "p_set", "L1")


def _project_on_disk(tmp_projects_dir, name: str = "Saved"):
    """Save a project whose stored series is WIDER than its snapshots."""
    n = pypsa.Network()
    idx = NARROW.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=pd.Series([100.0] * len(idx), index=idx))

    ctx = ProjectContext(network=n)
    ctx.loaded_project = name
    ctx.user_ts[KEY] = pd.Series([float(i) for i in range(len(WIDE))], index=WIDE)
    _save_context(ctx, name, expect=name)

    sidecar = tmp_projects_dir / name / "user_ts.json"
    assert sidecar.exists(), "fixture did not write a sidecar to hydrate from"
    saved = json.loads(sidecar.read_text())["loads"]["p_set"]["L1"]["values"]
    assert len(saved) == len(WIDE), (
        f"fixture precondition: the sidecar should hold the WIDE series "
        f"({len(WIDE)} rows), got {len(saved)}"
    )
    return tmp_projects_dir / name


def test_a_hydrated_context_holds_the_projects_own_series(tmp_projects_dir):
    """The property itself: hydrating loads that project's sidecar into it."""
    src = _project_on_disk(tmp_projects_dir)

    fresh = ProjectContext(network=pypsa.Network())
    _hydrate_context_from_disk(fresh, src, "Saved")

    assert KEY in fresh.user_ts, (
        "a hydrated context has an empty user time-series store, so the "
        "project's uploaded series is invisible to it"
    )
    assert len(fresh.user_ts[KEY]) == len(WIDE), (
        f"the hydrated series was truncated to the network's snapshots "
        f"(got {len(fresh.user_ts[KEY])} rows, sidecar holds {len(WIDE)})"
    )


def test_hydrating_one_project_leaves_another_contexts_store_alone(tmp_projects_dir):
    """
    The tenancy property has to survive this. `_restore_user_ts` REPLACES a
    store wholesale, and it was pointed at a process global — which is the
    single reason this restore was not done years ago. It must now write only
    into the context being hydrated.
    """
    src = _project_on_disk(tmp_projects_dir)

    bystander = ProjectContext(network=pypsa.Network())
    bystander.user_ts[("loads", "p_set", "Mine")] = pd.Series([9.0], index=NARROW[:1])
    before = dict(bystander.user_ts)

    fresh = ProjectContext(network=pypsa.Network())
    _hydrate_context_from_disk(fresh, src, "Saved")

    assert dict(bystander.user_ts) == before, (
        "hydrating one project's context wrote into another's store"
    )
    assert KEY not in bystander.user_ts


def test_opening_a_project_and_saving_it_does_not_narrow_its_series(tmp_projects_dir):
    """
    The data loss, end to end, and the reason this is not cosmetic.

    Hydrate (which is what a cold activate and the per-session resolver both
    do), then save — the ordinary consequence of opening a project and pressing
    Ctrl+S without touching anything. With an empty store,
    `_backup_network_ts_to_user_ts` ingests the 4-row `_t` column and the save
    serialises THAT over the 8-row series on disk.
    """
    src = _project_on_disk(tmp_projects_dir)

    reopened = ProjectContext(network=pypsa.Network())
    _hydrate_context_from_disk(reopened, src, "Saved")
    _save_context(reopened, "Saved", expect="Saved")

    values = json.loads((src / "user_ts.json").read_text())["loads"]["p_set"]["L1"]["values"]
    assert len(values) == len(WIDE), (
        f"re-saving a project that was only opened narrowed its stored series "
        f"from {len(WIDE)} rows to {len(values)} — the part of the upload that "
        f"lies outside the saved snapshot range was destroyed"
    )


def test_a_project_with_no_sidecar_leaves_the_store_empty(tmp_projects_dir):
    """
    Restore-OR-CLEAR, not restore-if-present.

    Every caller builds a fresh context today, so the clear is defensive — but
    `_hydrate_context_from_disk` means "make this context describe the project at
    `src`", and a context that silently kept a previous project's series while
    claiming to be this one is the exact shape of the defect this whole
    workstream removed. Pinned so a "only restore when the file exists"
    simplification cannot land unnoticed.
    """
    src = _project_on_disk(tmp_projects_dir, "NoSidecar")
    (src / "user_ts.json").unlink()

    reused = ProjectContext(network=pypsa.Network())
    reused.user_ts[("loads", "p_set", "Leftover")] = pd.Series([1.0], index=NARROW[:1])
    _hydrate_context_from_disk(reused, src, "NoSidecar")

    assert reused.user_ts == {}, (
        f"hydrating a project with no sidecar left a previous project's series "
        f"in the context (kept {sorted(reused.user_ts)})"
    )


def test_an_unreadable_sidecar_is_tolerated_and_not_overwritten(tmp_projects_dir):
    """
    The narrow case that still justifies `holds_user_series` over plain `True`.

    Hydrating must TOLERATE a corrupt `user_ts.json` — it runs twice per
    authenticated request on every route, so raising would 500 the whole app for
    one bad file — and tolerating it leaves the store empty. If an unattended
    save then persisted unconditionally, it would rebuild a narrower sidecar from
    the `_t` tables and write it over the only copy of whatever the file held.
    Answering "this context holds nothing" leaves the bytes on disk for a human.

    Before `_hydrate_context_from_disk` restored the sidecar at all, EVERY
    hydrated context looked like this one; now it is only the unreadable case,
    which is why this test exists rather than the broader one it replaced.
    """
    from services.project_context import holds_user_series

    src = _project_on_disk(tmp_projects_dir, "Corrupt")
    sidecar = src / "user_ts.json"
    sidecar.write_text("{ this is not json")

    ctx = ProjectContext(network=pypsa.Network())
    _hydrate_context_from_disk(ctx, src, "Corrupt")          # must not raise
    assert ctx.user_ts == {}, "a corrupt sidecar was partially believed"

    _save_context(ctx, "Corrupt", expect="Corrupt",
                  persist_user_ts=holds_user_series(ctx))

    assert sidecar.read_text() == "{ this is not json", (
        "an unattended save overwrote a sidecar it could not read, destroying "
        "the only copy of its contents"
    )
