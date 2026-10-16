"""
The Class-B ranking on an islanded copy (#313).

``fmea_top`` removes the pack's closed import Links, then solves that copy.
When one of those Links is the commercial PoC, the copy no longer has the
Link the binding names, and the sweep solves with ``commercial=None``. Any
other binding — a PoC that is still in the copy, no closed Links, no PoC,
or a commercial value that is not a mapping — is handed to the sweep as the
study stored it. The study's own config is never rewritten, so a later
stage still sees the tariff.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from models.energy_hub import PipelineStageRecord
from services.adequacy import eh_study as ES
from services.adequacy import sweep as SW
from services.solver_service import SolverConfig

_TARIFF = {"rate": 1}


def _run(monkeypatch, closed, commercial):
    copy = SimpleNamespace(removed=None)

    def remove(kind, names):
        copy.removed = (kind, list(names))

    copy.remove = remove
    seen = {}

    def contingencies(network):
        seen["contingencies"] = network
        return ["L1"]

    def sweep(network, lock, cfg, **kwargs):
        seen["network"] = network
        seen["cfg"] = cfg
        seen["kwargs"] = kwargs
        return [], {"aborted": False}

    monkeypatch.setattr(ES, "_private_copy", lambda network: copy)
    monkeypatch.setattr(ES, "_closed_import_links", lambda network, pack: list(closed))
    monkeypatch.setattr(ES, "_class_a_block", lambda *args, **kwargs: {"status": "not_established"})
    monkeypatch.setattr(SW, "class_b_contingencies", contingencies)
    monkeypatch.setattr(SW, "run_class_b_sweep", sweep)
    cfg = SolverConfig(commercial=commercial)
    study = ES._Study(
        network=object(),
        pack=SimpleNamespace(fmea_top_n=3),
        cfg=cfg,
        lock=object(),
        log_queue=object(),
        stop_event=object(),
        records=[PipelineStageRecord(stage="fmea_top")],
        sections={},
        budget_solves=10,
        solves=0,
    )
    ES._stage_fmea_top(study)
    return study, copy, seen


@pytest.mark.parametrize(
    "closed,commercial,dropped",
    [
        (["subsea_tie"], {"poc_link": "subsea_tie", "import_tariff": _TARIFF}, True),
        (["other_tie", "subsea_tie"], {"poc_link": "subsea_tie"}, True),
        (["grid_import"], {"poc_link": "subsea_tie", "import_tariff": _TARIFF}, False),
        ([], {"poc_link": "subsea_tie", "import_tariff": _TARIFF}, False),
        (["subsea_tie"], {"import_tariff": _TARIFF}, False),
        (["subsea_tie"], None, False),
        (["subsea_tie"], "subsea_tie", False),
    ],
)
def test_the_islanded_sweep_drops_commercial_only_when_the_closed_links_include_the_poc(
        monkeypatch, closed, commercial, dropped):
    study, copy, seen = _run(monkeypatch, closed, commercial)
    handed = seen["cfg"]
    assert seen["network"] is copy
    assert seen["kwargs"]["restore_base"] is False
    if dropped:
        assert handed is not study.cfg
        assert handed.commercial is None
    else:
        assert handed is study.cfg
        assert handed.commercial is commercial
    # A later stage still reads the binding the study was given.
    assert study.cfg.commercial is commercial
    if closed:
        assert copy.removed == ("Link", list(closed))
    else:
        assert copy.removed is None
    assert study.sections["fmea_top"][1]["excluded_closed_import_links"] == list(closed)
