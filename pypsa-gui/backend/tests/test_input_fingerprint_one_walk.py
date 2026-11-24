"""Issue 225: both input fingerprints from one walk of the network, byte-identical to the two walks.

`workflow_tools.one_input_walk()` serves the second form from the first read's walk; `_plain_frame_json`
writes a frame with no columns from an empty copy; `_exact_frame_json(indexes=)` reuses an index's text for an
index of the same bytes. Each is compared with `==` against the text or digest master writes (the bit-identity
rule): the templates, a full-year network, and frames of inexact floats, NaN, ±inf, -0.0, mixed dtypes and
object columns."""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import run_envelope, workflow_tools as W
from services.solver_service import SolverConfig

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TEMPLATES = sorted(p.name for p in (BACKEND / "project_templates").iterdir() if p.is_dir() and p.name.startswith("eh_"))
STAMPS = pd.date_range("2030-01-01", periods=6, freq="h")
INEXACT = [0.187, 0.4, 0.1 + 0.2, np.nan, np.inf, -np.inf, -0.0, 0.0, 1e-300, 5e-324]


def frames():
    """Frames whose text is pinned (`test_the_exact_text_is_masters`): every dtype path of `_exact_frame_json`
    and indexes that look alike but differ in their bytes."""
    periods = pd.MultiIndex.from_product([[2027, 2028], STAMPS[:3]], names=["period", "timestep"])
    floats = pd.MultiIndex.from_product([[2027.0, 2028.0], STAMPS[:3]], names=["period", "timestep"])
    return [
        pd.DataFrame({"x": INEXACT}, index=pd.date_range("2030-01-01", periods=len(INEXACT), freq="h")),
        pd.DataFrame({"mixed": pd.Series([1, 0.187, True, "s", None, pd.Timestamp("2030-01-01"), (1, 2.5)],
                                         dtype=object),
                      "i32": np.arange(7, dtype="int32"), "u8": np.arange(7, dtype="uint8"),
                      "b": [True, False] * 3 + [True], "f32": np.float32([0.187] * 7)}),
        pd.DataFrame(index=periods),
        pd.DataFrame(index=floats),
        pd.DataFrame({"a": np.arange(6.0)}, index=periods),
        pd.DataFrame(index=STAMPS),
        pd.DataFrame(index=STAMPS.tz_localize("UTC")),
        pd.DataFrame(index=STAMPS.as_unit("s")),
        pd.DataFrame({"z": [1.0, 2.0]}, index=pd.Index([-0.0, 1.0])),
        pd.DataFrame({"z": [1.0, 2.0]}, index=pd.Index([0.0, 1.0])),
        pd.DataFrame({"z": [1.0, 2.0]}, index=pd.Index([1, 2])),
        pd.DataFrame({"z": [1.0, 2.0]}, index=pd.Index([1.0, 2.0])),
        pd.DataFrame(index=pd.DatetimeIndex([STAMPS[0], pd.NaT])),
        pd.DataFrame(index=pd.MultiIndex.from_arrays([[1, np.nan], [STAMPS[0], STAMPS[1]]])),
        pd.DataFrame({"s": ["a", "b"]}, index=pd.Index(["x", "y"], name="name")),
        pd.DataFrame(),
        pd.DataFrame(columns=["c"], dtype=float),
    ]


# sha256 of "\n".join(_exact_frame_json(f) for f in frames()), written by master 5e8cbcdc (no memo).
EXACT_TEXT = "be83d7022a605c200de5a0fcf91c0029a608e173677b988dcdbecf97809bdd29"


def test_the_exact_text_is_masters():
    text = "\n".join(W._exact_frame_json(f) for f in frames())
    assert hashlib.sha256(text.encode()).hexdigest() == EXACT_TEXT


def test_a_reused_index_text_is_the_text_of_that_index():
    """Every frame, read after every other one (and itself) in one memo, writes its own text."""
    alone = [W._exact_frame_json(f) for f in frames()]
    indexes = {}
    for _ in range(2):
        for f, text in zip(frames(), alone):
            assert W._exact_frame_json(f, indexes=indexes) == text
        for f, text in zip(reversed(frames()), reversed(alone)):
            assert W._exact_frame_json(f, indexes=indexes) == text


def test_indexes_of_other_bytes_never_share_a_text():
    a, b = (pd.DataFrame(index=pd.Index(v)) for v in ([-0.0, 1.0], [0.0, 1.0]))
    indexes = {}
    assert W._exact_frame_json(a, indexes=indexes) != W._exact_frame_json(b, indexes=indexes)
    assert len(indexes) == 2


@pytest.mark.parametrize("iso", [False, True])
def test_a_frame_with_no_columns_writes_what_pandas_writes(iso):
    for f in frames() + [pd.DataFrame(index=pd.MultiIndex.from_product([[2027], STAMPS]))]:
        kwargs = {"date_format": "iso"} if iso else {}
        assert W._plain_frame_json(f, iso) == f.to_json(**kwargs)


def _ctx(n, config=None, user_ts=None):
    return types.SimpleNamespace(network=n, solver_state={"solver_config": config or SolverConfig()},
                                 storage_dir=None, user_ts=user_ts or {}, network_revision=0)


def edge_network():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], STAMPS[:3]]))
    n.investment_period_weightings.loc[:, "years"] = [10.0, 0.1]
    n.add("Bus", ["b0", "b1"], carrier="AC")
    n.add("Carrier", "gas", co2_emissions=0.187)
    n.add("Generator", "g0", bus="b0", carrier="gas", p_nom=-0.0, marginal_cost=0.187, capital_cost=0.4,
          p_nom_extendable=True)
    n.add("Generator", "g1", bus="b1", carrier="gas", p_nom=np.inf, marginal_cost=0.1 + 0.2, p_nom_max=-np.inf)
    n.add("Load", "l0", bus="b0", p_set=[0.187, np.nan, np.inf, -np.inf, -0.0, 1e-300])
    n.add("Line", "ln", bus0="b0", bus1="b1", x=0.4, r=1 / 3, s_nom=5)
    n.generators["p_nom_min"] = pd.Series([1, True], index=n.generators.index, dtype=object)
    n.generators["build_year"] = n.generators["build_year"].astype("int32")
    n.generators_t.p_max_pu = pd.DataFrame({"g0": [0.187, np.nan, 0.4, np.inf, -0.0, 0.3]}, index=n.snapshots)
    return _ctx(n, user_ts={"load_x": pd.Series([0.187, np.nan, 2.0], index=STAMPS[:3])})


def template_ctx(name):
    import importlib

    for path in sorted((BACKEND / "project_templates").glob("eh_*.py")):
        module = importlib.import_module(path.stem)
        if hasattr(module, f"build_{name}"):
            build = getattr(module, f"build_{name}")
            try:
                n = build()
            except TypeError:  # the builders that take the templates module
                n = build(T)
            return _ctx(n, SolverConfig(**T.solver_config(name)))
    raise LookupError(name)


def full_year_ctx():
    import eh_concentrator as C

    return _ctx(C.build_eh_concentrator_staged(T, years=(2027,)),
                SolverConfig(**T.solver_config("eh_concentrator_staged")))


def _walks(monkeypatch):
    calls = []
    real = W._input_digests
    monkeypatch.setattr(W, "_input_digests", lambda ctx, forms: calls.append(forms) or real(ctx, forms))
    return calls


@pytest.mark.parametrize("build", [*(lambda name=name: template_ctx(name) for name in TEMPLATES), full_year_ctx,
                                   edge_network], ids=[*TEMPLATES, "full_year", "edge"])
def test_one_walk_gives_each_form_its_own_walks_digest(build, monkeypatch):
    ctx = build()
    plain, exact = W._network_input_fingerprint(ctx), W._network_input_fingerprint(ctx, exact=True)
    walks = _walks(monkeypatch)
    for order in ((False, True), (True, False)):
        with W.one_input_walk():
            got = {form: W._network_input_fingerprint(ctx, exact=form) for form in order}
        assert got == {False: plain, True: exact}
    assert walks == [(False, True), (False, True)]
    walks.clear()
    found = run_envelope.identity(ctx)
    assert (found["fingerprint"], found["exact"]) == (plain, exact) and walks == [(False, True)]
    walks.clear()
    found = run_envelope.identity(ctx, exact=False)
    assert (found["fingerprint"], found["exact"]) == (plain, None) and walks == [(False,)]


def test_the_kept_form_goes_once_to_its_own_context(monkeypatch):
    ctx, other = edge_network(), edge_network()
    other.network = ctx.network
    walks = _walks(monkeypatch)
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        W._network_input_fingerprint(other, exact=True)  # another context: its own walk
        W._network_input_fingerprint(ctx, exact=True)  # kept
        W._network_input_fingerprint(ctx, exact=True)  # served once: walks again
    assert walks == [(False, True)] * 3
    walks.clear()
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        ctx.network = ctx.network.copy()  # another network on the same context: never the kept form
        W._network_input_fingerprint(ctx, exact=True)
    assert walks == [(False, True)] * 2
    W._network_input_fingerprint(ctx, exact=True)  # outside the scope: one form
    assert walks[-1] == (True,)


def test_an_exact_form_that_refuses_leaves_the_plain_form_as_it_was():
    ctx = edge_network()
    ctx.network.generators["p_nom_min"] = pd.Series([{"a": 1}, 1], index=ctx.network.generators.index,
                                                    dtype=object)
    plain = W._network_input_fingerprint(ctx)
    with pytest.raises(Exception) as alone:
        W._network_input_fingerprint(ctx, exact=True)
    with W.one_input_walk():
        assert W._network_input_fingerprint(ctx) == plain
        with pytest.raises(type(alone.value)):
            W._network_input_fingerprint(ctx, exact=True)
    found = run_envelope.identity(ctx)
    assert (found["fingerprint"], found["exact"]) == (plain, None)
