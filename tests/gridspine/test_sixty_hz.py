"""60 Hz throughout (issue datacenter-grid-readiness 09).

Oracles:
* per unit: M = 2 H S_n / f0 and K = P_n / (R f0), so the same island at 50
  and 60 Hz gives the same Δf / f0, RoCoF / f0 and droop response (each
  unit's ΔP), the same ΣK·f0, the same droop cut ΔK·f0 and the same E4
  block metrics per unit;
* the campus driver threads the network's ``f_hz`` into every simulate,
  judge, trip floor, EMT flag and equipment call of the frequency check, the
  cuts and the GFM settings;
* the RAW export writes a 60 Hz campus's base frequency;
* no study path in ``gridspine/static``, ``gridspine/drivers`` or
  ``gridspine/producers`` carries a 50 Hz constant outside a default argument.
"""
import copy
import pathlib
import re

import numpy as np
import pytest

from gridspine.drivers import campus_study as D
from gridspine.static import frequency as F
from tests.gridspine.test_campus_bundle import bundle, read
from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run
from tests.gridspine.test_frequency import bess, gen
from tests.gridspine.test_island_flow import spec

ROOT = pathlib.Path(__file__).resolve().parents[2]
FS = (50.0, 60.0)


def island():
    """A genset, a droop GFM and a VSM: every model whose M and K carry f0."""
    return [gen(p0=0.0, p_max=1e6), bess(name="B1", i_max_mw=1e9), bess("gfm_vsm", name="B2", i_max_mw=1e9)]


def scaled_limits(f0, band_pu=0.01):
    """``test_frequency_cuts.lim`` in per unit of f0: wide, the band ``band_pu``."""
    one = {"rocof_hz_per_s": 1e6, "f_min_hz": 0.8 * f0, "f_max_hz": 1.2 * f0, "qss_band_hz": 2.0 * f0}
    return {"transition": dict(one), "steady_island": {**one, "qss_band_hz": band_pu * f0}}


@pytest.fixture(scope="module")
def traces():
    return {f0: F.simulate(island(), F.step(4.0), t_end=10.0, dt=1e-3, f0=f0) for f0 in FS}


def test_the_frequency_deviation_and_rocof_are_equal_per_unit(traces):
    a, b = traces[50.0], traces[60.0]
    assert np.allclose(a.t, b.t)
    assert np.allclose(a.df / 50.0, b.df / 60.0, rtol=1e-9, atol=1e-12)
    assert a.rocof0 / 50.0 == pytest.approx(b.rocof0 / 60.0, rel=1e-12)
    ma, mb = F.metrics(a), F.metrics(b)
    for name in ("rocof_window_hz_per_s", "nadir_hz", "df_qss_hz"):
        assert getattr(ma, name) / 50.0 == pytest.approx(getattr(mb, name) / 60.0, rel=1e-9), name


def test_the_droop_response_is_equal_per_unit(traces):
    a, b = traces[50.0], traces[60.0]
    for name in ("G", "B1", "B2"):
        assert np.allclose(a.dp[name], b.dp[name], rtol=1e-9, atol=1e-12), name
    # the droop gain is P_n / (R f0): ΣK f0 is the same island at either frequency
    k = {f0: F._response(island(), f0)[1] for f0 in FS}
    assert k[50.0] * 50.0 == pytest.approx(k[60.0] * 60.0, rel=1e-12)
    assert k[60.0] == pytest.approx(10.0 / (0.04 * 60.0) * 3, rel=1e-12)


def test_the_droop_cut_is_equal_per_unit():
    dk = {}
    for f0 in FS:
        def run(us, t_end=10.0, f0=f0):
            return F.simulate(us, F.step(4.0), t_end=t_end, dt=5e-3, f0=f0)
        cuts = {c[0]: c for c in F.cuts_for_event(island(), run, scaled_limits(f0, band_pu=0.001), f0)}
        dk[f0] = cuts["droop_gain"][1]
    assert dk[50.0] > 0
    assert dk[50.0] * 50.0 == pytest.approx(dk[60.0] * 60.0, rel=2e-3)


def test_the_e4_pickup_is_equal_per_unit():
    gens = [gen(name="G1", p0=0.0, p_max=1e6), gen(name="G2", p0=0.0, p_max=1e6)]
    res = {f0: F.e4_ups_bridged(gens, [2.0, 2.0], band_hz=0.002 * f0, settle_hz_per_s=0.001 * f0,
                                     t_end=60.0, f0=f0, steady_stop=False) for f0 in FS}
    assert res[50.0].complete and res[60.0].complete
    assert res[50.0].block_times_s == pytest.approx(res[60.0].block_times_s, rel=1e-6)
    for ma, mb in zip(res[50.0].blocks, res[60.0].blocks):
        assert ma.nadir_hz / 50.0 == pytest.approx(mb.nadir_hz / 60.0, rel=1e-6)
        assert ma.df_qss_hz / 50.0 == pytest.approx(mb.df_qss_hz / 60.0, rel=1e-6)


def sixty(s):
    """``s`` as a 60 Hz campus, its frequency limits and UPS window scaled from 50 Hz."""
    s = copy.deepcopy(s)
    c = s["campus"]
    c["f_hz"] = 60
    for part in c["island"]["frequency_limits"].values():
        for key in ("f_min_hz", "f_max_hz", "qss_band_hz", "rocof_hz_per_s"):
            part[key]["value"] = part[key]["value"] * 1.2
    for u in c["units"].values():
        if "f_window_hz" in u.get("island", {}):
            u["island"]["f_window_hz"]["value"] *= 1.2
    return s


def test_the_campus_driver_threads_the_networks_frequency(tmp_path, monkeypatch):
    seen = []

    def spy(name, pos):
        orig = getattr(F, name)

        def wrapped(*a, **k):
            seen.append((name, k["f0"] if "f0" in k else (a[pos] if len(a) > pos else "default")))
            return orig(*a, **k)
        monkeypatch.setattr(F, name, wrapped)
    for name, pos in (("simulate", 4), ("judge", 2), ("trip_floor", 2), ("emt_flags", 2), ("equipment", 1),
                      ("_response", 1)):
        spy(name, pos)

    run = write_run(tmp_path / "run", sixty(campus_spec(scenarios=("seamless", "ups_bridged"))))
    D.island_frequency_campus(run)
    D.cuts_campus(run)
    D.gfm_settings_campus(run, max_sims=20)
    names = {n for n, _ in seen}
    assert {"simulate", "judge", "trip_floor", "emt_flags", "equipment"} <= names
    assert {f0 for _, f0 in seen} == {60.0}, sorted({(n, repr(f0)) for n, f0 in seen if f0 != 60.0})


def test_a_60_hz_campus_writes_60_hz_in_the_raw_header(tmp_path):
    s = spec()
    s["campus"]["f_hz"] = 60
    _, b = bundle(tmp_path, s)
    raw = next(n for n in (p.name for p in b.path.iterdir()) if n.endswith(".raw"))
    header = read(b, raw).splitlines()[0]
    assert header.split(",")[5].strip() == "60.00"


#: A 50 Hz literal (50, 50.0), 100π or 2π·50.
FIFTY = re.compile(r"(?<![\w.])50(\.0*)?(?![\w.])|314\.1|pi\s*\*\s*50\b")
#: Lines where a 50 Hz value is the stated default or a table keyed by frequency, not an assumption.
ALLOWED = (
    re.compile(r"^F0 = 50\.0$"),                                   # frequency.py: the default for callers that state nothing
    re.compile(r"^PEAK_FACTOR = \{50: 2\.5, 60: 2\.6\}$"),         # campus_sc.py: IEC 62271's peak factor by frequency
    re.compile(r"^retry_max_iteration: int = 50$"),                # voltage_stability.py: a load-flow iteration count, not a frequency
    re.compile(r"^H_MIN, H_MAX = 2, 50$"),                          # harmonics.py: IEC 61000-3-6's orders 2..50, not a frequency
    # producers/campus.py: a typical LV fault level in MVA
    re.compile(r"^TYPICAL_SK_MVA = \(\(220\.0, 10000\.0, 0\.1\), \(60\.0, 3000\.0, 0\.1\), \(10\.0, 300\.0, 0\.3\), \(0\.0, 50\.0, 0\.5\)\)$"),
    re.compile(r'^"load_step_max_pct": 50\.0, "cos_phi_r"'),      # producers/campus.py: a genset load step in percent
    # current_tdd.py: IEEE 519's Isc/IL band bounds, not a frequency
    re.compile(r"^ISC_IL_BOUNDS = \(20\.0, 50\.0, 100\.0, 1000\.0\)$"),
    re.compile(r"^ISC_IL_BOUNDS_T4 = \(25\.0, 50\.0\)$"),
)
#: A default argument in a signature: ``name=50.0`` or ``name: float = 50.0``.
DEFAULT = re.compile(r"\w+\s*(:\s*\w+\s*)?=\s*50(\.0*)?\s*[,)]")


def _code_lines(path):
    """``(line number, code, in a def signature)`` of ``path``, docstrings
    and comments dropped (no '#' sits inside a string beside a 50 here)."""
    in_doc, in_sig = False, False
    for i, line in enumerate(path.read_text().splitlines(), 1):
        parts = line.split('"""')
        code = "".join(part for j, part in enumerate(parts) if (j % 2 == 0) != in_doc)
        if len(parts) % 2 == 0:
            in_doc = not in_doc
        code = code.split("#", 1)[0].strip()
        in_sig = in_sig or code.startswith(("def ", "async def "))
        yield i, code, in_sig
        if in_sig and code.endswith(":"):
            in_sig = False


def _fifty(path):
    """``[(line number, code)]`` of ``path`` carrying a 50 Hz value outside a default argument."""
    return [(i, code) for i, code, in_sig in _code_lines(path)
            if FIFTY.search(code) and not any(a.match(code) for a in ALLOWED)
            and not (in_sig and not FIFTY.search(DEFAULT.sub("", code)))]


def test_no_study_path_carries_a_50_hz_constant_outside_a_default():
    hits = [f"{path.relative_to(ROOT)}:{i}: {code}"
            for folder in ("gridspine/static", "gridspine/drivers", "gridspine/producers")
            for path in sorted((ROOT / folder).rglob("*.py")) for i, code in _fifty(path)]
    assert not hits, "\n".join(hits)


def test_the_scan_catches_what_it_should(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('def f(x, f0=50.0,\n      y=1):\n    """at 50 Hz"""\n    return simulate(x, f0=50.0) + 2 * math.pi * 50\n'
                 'w = 2.0 * math.pi * 50  # 50 Hz\nk = p / (r * 50)\n')
    assert [i for i, _ in _fifty(p)] == [4, 5, 6]
