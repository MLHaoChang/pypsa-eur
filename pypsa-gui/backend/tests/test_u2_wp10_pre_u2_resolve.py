"""
U2 WP10, gate WP6 W4 (second half): an Expert re-solve of a network a
decision study wrote before U2 refuses; it never drops the demand charge
while keeping the tariff's prices in the Links.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md,
WP10 "Also (gate WP6 W4, second half)".

Before U2 a study's base and option forks carried the tariff in two pieces:
the energy, network and export prices as PERMANENT ``links_t.marginal_cost``
on ``grid_import`` / ``grid_export``, and the demand charge as
``SolverConfig.demand_charge``, which GS's LP wrapper read. WP10 deletes that
field and the wrapper, and ``_solver_config_from_dict`` (the one loader of a
saved ``solver_config.json``: project load, bundle import, the queue's
snapshot) drops a key the dataclass no longer has. Re-solved as is, such a
network would keep its energy prices and lose its demand charge: a cheaper
tariff than the study's, with nothing said. The refusal reads the NETWORK
(a decision-study pack whose meter Links carry a price), not the config,
because any save of the project rewrites the config without the old key.

``tests/fixtures/pre_u2_fork/`` holds a base and a solved option fork written
the way the old code wrote them (``make_pre_u2_fork.py`` is the record).
"""
from __future__ import annotations

import json
import pathlib
import queue
import shutil
import threading

import pypsa
import pytest

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "pre_u2_fork"
CODE = "study_network_tariff_in_links"


def _stored(tmp_path: pathlib.Path, sub: str):
    """The stored project as the Expert opens it: its netCDF and its config, via the loader."""
    from routers.projects import _solver_config_from_dict

    d = tmp_path / sub
    shutil.copytree(FIXTURE / sub, d)
    n = pypsa.Network(str(d / "network.nc"))
    raw = json.loads((d / "solver_config.json").read_text(encoding="utf-8"))
    return n, _solver_config_from_dict(raw), raw


def _solve(cfg, n) -> tuple[tuple[str, str], list[str]]:
    from services.solver_service import run_simulation

    q: queue.SimpleQueue = queue.SimpleQueue()
    status = run_simulation(cfg, n, threading.RLock(), threading.Event(), q)
    lines = []
    while not q.empty():
        item = q.get()
        if item is not None:
            lines.append(str(item))
    return status, lines


def test_the_fixture_is_a_pre_u2_network():
    """The stored pieces are what W4 names: the demand charge in the config, prices in the Links."""
    for sub in ("base", "fork"):
        raw = json.loads((FIXTURE / sub / "solver_config.json").read_text(encoding="utf-8"))
        assert raw["demand_charge"]["price_per_mw_per_period"] > 0
        assert raw.get("commercial") is None
        n = pypsa.Network(str(FIXTURE / sub / "network.nc"))
        mc = n.links_t.marginal_cost
        assert (mc["grid_import"] > 0).all() and (mc["grid_export"] < 0).all()
        assert "decision_study_pack" in n.meta


@pytest.mark.parametrize("sub", ["base", "fork"])
@pytest.mark.live_solve
def test_an_expert_resolve_of_a_stored_pre_u2_network_refuses(tmp_path, sub):
    n, cfg, _raw = _stored(tmp_path, sub)
    before = n.links_t.marginal_cost.copy()
    p_before = n.storage_units.get("p_nom_opt")
    (status, lines) = _solve(cfg, n)
    assert status == ("error", "validation_failed"), (status, lines[-5:])
    refusal = [ln for ln in lines if f"[{CODE}]" in ln]
    assert refusal, lines[-10:]
    # Plain words, with the way out.
    assert "re-run the study" in refusal[0].lower()
    assert "demand charge" in refusal[0].lower()
    # Nothing was solved or rewritten.
    assert n.links_t.marginal_cost.equals(before)
    if p_before is not None:
        assert n.storage_units["p_nom_opt"].equals(p_before)


@pytest.mark.live_solve
@pytest.mark.parametrize("variant", ["as_stored", "config_resaved", "commercial_added"])
def test_the_demand_charge_is_never_dropped_while_the_link_prices_stay(tmp_path, variant):
    """
    Whatever the config now says — the file as stored, the file re-saved
    without the old key (any save does that once the field is gone), or an
    Expert who adds an engine commercial block on top (which would price the
    tariff a second time) — the network's Link prices make the solve refuse.
    """
    from routers.projects import _solver_config_from_dict

    n, cfg, raw = _stored(tmp_path, "base")
    if variant == "config_resaved":
        cfg = _solver_config_from_dict({k: v for k, v in raw.items() if k != "demand_charge"})
    elif variant == "commercial_added":
        import dataclasses

        from services.study import compile as C
        from services.study import library as L
        from services.study import questions as Q
        from tests.golden import site_fixture as sf
        from tests.u2_targets import FAKE_REF, flat_resolver

        defaults = L.load_defaults()
        ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults)
        compiled = C.commercial_from_ledger(sf.site_intake(), ledger, defaults, n.snapshots,
                                            export_series=FAKE_REF)
        compiled = C.bind_on_network(n, compiled, resolve_ref=flat_resolver(40.0, n.snapshots))
        cfg = dataclasses.replace(cfg, commercial=compiled.commercial())
    (status, lines) = _solve(cfg, n)
    assert status == ("error", "validation_failed"), (variant, status, lines[-5:])
    assert any(f"[{CODE}]" in ln for ln in lines), lines[-10:]


def test_the_preflight_names_the_links_and_the_way_out(tmp_path):
    from services.validation_service import validate_for_run

    n, cfg, _raw = _stored(tmp_path, "fork")
    issues = [i for i in validate_for_run(n, cfg) if i.code == CODE]
    assert len(issues) == 1
    [issue] = issues
    assert issue.severity == "error"
    assert "grid_import" in issue.message and "grid_export" in issue.message
    assert "re-run the study" in issue.message.lower()


def test_a_network_the_study_writes_now_is_not_refused():
    """Since U2 WP6 the pack writes no price: the engine prices the meter at solve."""
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from services.validation_service import validate_for_run
    from tests.golden import site_fixture as sf

    defaults = L.load_defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults)
    n = packs.build_site_network(sf.site_intake(), ledger, "bess_2h", library=defaults)
    cfg = packs.option_solver_config(ledger, packs.option_commercial(
        sf.site_intake(), ledger, defaults, n.snapshots))
    assert CODE not in {i.code for i in validate_for_run(n, cfg)}


def test_a_network_without_the_study_pack_is_not_the_studys_business(tmp_path):
    """An ordinary network with priced Links is the user's tariff, not a study's: not refused."""
    from services.validation_service import validate_for_run

    n, cfg, _raw = _stored(tmp_path, "base")
    del n.meta["decision_study_pack"]
    assert CODE not in {i.code for i in validate_for_run(n, cfg)}


def test_the_detector_reads_the_packs_own_names():
    from services.study import packs, pre_u2

    assert pre_u2.PACK_META_KEY == packs.PACK_META_KEY
    assert pre_u2.METER_LINKS == (packs.IMPORT_LINK, packs.EXPORT_LINK)
