"""DC 09b: the island MILP reads the campus's nominal frequency, not 50 Hz
(issue datacenter-grid-readiness 09, second half).

Oracles:
* the RoCoF rows need 2M ≥ f0·ΔP/RoCoF_max and the droop gain is
  K = P_n/(R·f0), so at the same Hz limits a 60 Hz island needs 60/50 the
  inertia and its droop-gain coefficient is 50/60 of the 50 Hz one;
* at the same per-unit limits (RoCoF/f0, qss band/f0) a 50 and a 60 Hz
  island size the same BESS and genset fleet;
* f0 comes from the project's campus file (``campus.f_hz``, gridspine's
  rule: 50 or 60, 50 when unstated), through ``campus_f_hz`` alone, on the
  solve path and in the baseline cache key; a campus save that changes it
  drops the island study;
* no line of ``island.py`` carries a 50 Hz value outside its stated default.
"""
import json
import pathlib
import re
import types

import pytest
import yaml

from services import campus_electrical_service as ce
from services import project_registry
from services.results import baseline
from services.solver import island as island_mod
from services.solver.island import IslandRefusal, _cut_coefficients, campus_f_hz
from services.solver_service import SolverConfig
from tests import test_campus_electrical_service as _ces
from tests.test_island_rows import (
    _BESS,
    _VSM,
    TOL,
    _a,
    _extendable_genset,
    _genset,
    _hub,
    _p_nom,
    _seamless,
    _solve,
    _units,
)

user_and_db, hub = _ces.user_and_db, _ces.hub          # the campus service's fixtures


def _at(island, f0):
    return {**island, "f_hz": f0}


def _campus(project_dir, text):
    path = pathlib.Path(project_dir) / ce.SUBDIR / ce.CAMPUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ── the rows scale with f0 ──────────────────────────────────────────────────


def test_the_rocof_row_needs_sixty_over_fifty_the_inertia_at_60_hz():
    """ΔP⁺ 4 MW, 1 Hz/s, H_v 4 s: 2·4·s ≥ f0·4 → 25 MVA at 50, 30 at 60."""
    sizes = {f0: _p_nom(_solve(_hub(load=4.0), _at(_seamless(_units(bess=_VSM), rocof=1.0), f0)))
             for f0 in (50.0, 60.0)}
    assert sizes[50.0] == pytest.approx(25.0, abs=TOL)
    assert sizes[60.0] / sizes[50.0] == pytest.approx(60.0 / 50.0, rel=1e-6)


def test_the_droop_gain_coefficient_is_fifty_over_sixty_at_60_hz():
    n = _hub(load=4.0)
    island = _seamless(_units())
    coef = {f0: dict((name, c) for name, _, c in _cut_coefficients(n, _at(island, f0))["droop_gain"])
            for f0 in (50.0, 60.0)}
    assert coef[50.0]["bess"] == pytest.approx(1.0 / (0.04 * 50.0))
    assert coef[60.0]["bess"] / coef[50.0]["bess"] == pytest.approx(50.0 / 60.0)


def test_the_facts_report_the_islands_f0():
    n = _solve(_hub(load=4.0), _at(_seamless(_units(bess=_VSM), rocof=1.0), 60.0))
    assert n._island_facts["instant"]["f0_hz"] == 60.0


def test_without_f_hz_the_island_is_50_hz():
    n = _solve(_hub(load=4.0), _seamless(_units(bess=_VSM), rocof=1.0))
    assert n._island_facts["instant"]["f0_hz"] == 50.0


# ── the same per-unit limits size the same island at 50 and 60 Hz ──────────


def _per_unit_pair(build, island, *, rocof=None, qss=None, comp="bess"):
    """``build()``'s network solved at 50 and 60 Hz with the limits given in
    Hz at 50 Hz and scaled by f0/50; the sized p_nom of ``comp`` at each."""
    out = {}
    for f0 in (50.0, 60.0):
        kw = {}
        if rocof is not None:
            kw["rocof"] = rocof * f0 / 50.0
        if qss is not None:
            kw["qss"] = qss * f0 / 50.0
        n = _solve(build(), _at(island(**kw), f0))
        out[f0] = (_p_nom(n) if comp == "bess" else float(n.generators.at[comp, "p_nom_opt"]))
    return out


@pytest.mark.parametrize("bess", [_VSM, _BESS], ids=["vsm", "droop"])
def test_the_rocof_rows_size_the_same_bess_per_unit(bess):
    sizes = _per_unit_pair(lambda: _hub(load=4.0), lambda **kw: _seamless(_units(bess=bess), **kw),
                           rocof=1.0)
    assert sizes[60.0] == pytest.approx(sizes[50.0], abs=TOL)


def test_the_qss_rows_size_the_same_bess_per_unit():
    """test_island_rows' 15 MW case (band 0.5 Hz at 50) at 60 Hz with a 0.6 Hz band."""
    gensets = {"g1": _genset(), "g2": _genset()}
    sizes = _per_unit_pair(lambda: _hub(load=4.0, gensets=[("g1", 2.0), ("g2", 1.0)]),
                           lambda **kw: _seamless(_units(gensets=gensets), k=1, **kw), qss=0.5)
    assert sizes[50.0] == pytest.approx(15.0, abs=TOL)
    assert sizes[60.0] == pytest.approx(15.0, abs=TOL)


def test_the_rocof_block_row_sizes_the_same_gensets_per_unit():
    """test_island_rows' ups_bridged 40 MW case at 60 Hz with 1.2 Hz/s."""
    def build():
        n = _hub(gensets=[("g1", 10.0)])
        _extendable_genset(n, "gx", p_nom_max=100.0)
        return n
    units = _units(bess=None, gensets={"g1": _genset(), "gx": _genset(unit_mw=_a(10.0))})
    sizes = _per_unit_pair(build, lambda **kw: _seamless(units, scenarios=("ups_bridged",), blocks=[3.0],
                                                         k=1, **kw), rocof=1.0, comp="gx")
    assert sizes[50.0] == pytest.approx(40.0, abs=TOL)
    assert sizes[60.0] == pytest.approx(40.0, abs=TOL)


def test_the_hourly_droop_gain_is_the_same_per_unit():
    """island_hourly's ΣK·f0 is the same at 50 and 60 Hz."""
    n = _solve(_hub(load=4.0), _seamless(_units(), rocof=1.0))
    k = {f0: island_mod.island_hourly(n, _at(_seamless(_units(), rocof=1.0), f0))[1]["k_mw_per_hz"]
         for f0 in (50.0, 60.0)}
    assert (k[50.0] * 50.0).to_numpy() == pytest.approx((k[60.0] * 60.0).to_numpy())


# ── where f0 comes from ─────────────────────────────────────────────────────


def test_a_project_without_a_campus_file_is_50_hz(tmp_path):
    assert campus_f_hz(tmp_path) == 50.0


def test_a_campus_without_f_hz_is_50_hz(tmp_path):
    _campus(tmp_path, "campus:\n  name: X\n")
    assert campus_f_hz(tmp_path) == 50.0


def test_a_60_hz_campus_is_60_hz(tmp_path):
    _campus(tmp_path, "campus:\n  f_hz: 60\n")
    assert campus_f_hz(tmp_path) == 60.0


@pytest.mark.parametrize("text, match", [
    ("campus:\n  f_hz: 55\n", "must be 50 or 60"),
    ("campus: [unclosed", "not valid YAML"),
    ("- a list\n", "no 'campus' mapping"),
])
def test_a_campus_that_states_no_valid_f_hz_is_refused(tmp_path, text, match):
    _campus(tmp_path, text)
    with pytest.raises(IslandRefusal, match=match):
        campus_f_hz(tmp_path)


def test_a_build_without_gridspine_refuses_a_campus_rather_than_assume_50_hz(
        tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_gridspine(name, *args, **kwargs):
        if name == "gridspine" or name.startswith("gridspine."):
            raise ImportError(f"No module named {name!r}")
        return real(name, *args, **kwargs)

    _campus(tmp_path, "campus:\n  f_hz: 60\n")
    monkeypatch.setattr(builtins, "__import__", no_gridspine)
    with pytest.raises(IslandRefusal, match="gridspine is not available in this build"):
        campus_f_hz(tmp_path)
    # Without a campus file nothing needs gridspine: 50 Hz as before.
    assert campus_f_hz(tmp_path / "no-campus") == 50.0


def _bind(monkeypatch, storage):
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(PyPSAService, "solve_target_context",
                        classmethod(lambda cls: types.SimpleNamespace(storage_dir=str(storage))))


def test_the_solve_path_carries_a_60_hz_campus(tmp_path, monkeypatch):
    from services.adequacy.island_config import SIDECAR_NAME

    from tests.test_campus_island_service import _config

    (tmp_path / SIDECAR_NAME).write_text(json.dumps(_config()))
    _campus(tmp_path, "campus:\n  f_hz: 60\n")
    _bind(monkeypatch, tmp_path)
    island = island_mod.load_project_island()
    assert island["f_hz"] == 60.0
    n = _hub(load=10.0, gensets=[("g", 20.0)])
    n.optimize(solver_name="highs", extra_functionality=island_mod.island_extra_functionality(island))
    assert n._island_facts["instant"]["f0_hz"] == 60.0


def test_the_baseline_key_changes_with_the_campus_f_hz(tmp_path):
    from services.adequacy.island_config import SIDECAR_NAME

    from tests.test_campus_island_service import _config

    (tmp_path / SIDECAR_NAME).write_text(json.dumps(_config()))
    plan = types.SimpleNamespace(removed=[], fixed=[])
    n = _hub(load=10.0)

    def key():
        return baseline._key(n, SolverConfig(), plan, baseline._island_sidecar(tmp_path))

    k50 = key()
    _campus(tmp_path, "campus:\n  f_hz: 60\n")
    assert key() != k50
    _campus(tmp_path, "campus:\n  f_hz: 50\n")
    assert key() == k50


def test_a_campus_save_that_changes_f_hz_drops_the_island_study(hub):
    spec = yaml.safe_load(ce.draft(hub)["campus_yaml"])
    study = ce._island_paths(hub)[1]
    study.write_text("{}")
    ce.save_campus(hub, yaml.safe_dump(spec))                # f0 unchanged: the study stays
    assert study.is_file()
    spec["campus"]["f_hz"] = 60
    ce.save_campus(hub, yaml.safe_dump(spec))
    assert not study.is_file()
    study.write_text("{}")
    ce.draft(hub, overwrite=True)                            # the redraft keeps the file's 60 Hz
    assert campus_f_hz(project_registry.project_dir(hub)) == 60.0
    assert study.is_file()                                   # f0 unchanged: the study stays


# ── no 50 Hz value outside the stated default ───────────────────────────────

#: A 50 Hz literal (50, 50.0), 100π or 2π·50.
FIFTY = re.compile(r"(?<![\w.])50(\.0*)?(?![\w.])|314\.1|pi\s*\*\s*50\b")
#: The exact lines that may carry one: the default for an island that states no f_hz.
ALLOWED = ("F0_HZ = 50.0",)
#: The exact lines that may read that default: the reader and the resolver's no-file case.
F0_READERS = ('return float(island.get("f_hz", F0_HZ))', "return F0_HZ")


def _code_lines(path):
    """``(line number, code)`` of ``path``, docstrings and comments dropped."""
    in_doc = False
    for i, line in enumerate(path.read_text().splitlines(), 1):
        parts = line.split('"""')
        code = "".join(part for j, part in enumerate(parts) if (j % 2 == 0) != in_doc)
        if len(parts) % 2 == 0:
            in_doc = not in_doc
        yield i, code.split("#", 1)[0].strip()


def _fifty(path):
    """``[(line number, code)]`` of ``path`` carrying a 50 Hz value, or
    reading ``F0_HZ``, outside the allowed lines."""
    return [(i, code) for i, code in _code_lines(path)
            if (FIFTY.search(code) and code not in ALLOWED)
            or ("F0_HZ" in code and code not in ALLOWED + F0_READERS)]


def test_island_py_carries_no_50_hz_outside_its_default():
    path = pathlib.Path(island_mod.__file__)
    hits = _fifty(path)
    assert not hits, "\n".join(f"island.py:{i}: {code}" for i, code in hits)
    code = [c for _, c in _code_lines(path)]
    assert {line: code.count(line) for line in ALLOWED + F0_READERS} == dict.fromkeys(ALLOWED + F0_READERS, 1)


def test_the_scan_catches_what_it_should(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('F0_HZ = 50.0\n"""at 50 Hz"""\nk = p / (r * 50)\nw = 2.0 * math.pi * 50  # 50 Hz\n'
                 'x = dp.scaled(F0_HZ / rocof)\nreturn F0_HZ\ny = 500.0 + 1.50\n')
    assert [i for i, _ in _fifty(p)] == [3, 4, 5]
