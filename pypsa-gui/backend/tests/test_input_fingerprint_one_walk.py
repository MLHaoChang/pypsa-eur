"""Issue 225: both input fingerprints from one walk of the network, byte-identical to the two walks.

`workflow_tools.one_input_walk()` serves the second form from the first read's walk; `_plain_frame_json`
writes a frame with no columns from an empty copy; `_exact_frame_json(indexes=)` reuses an index's text for an
index of the same bytes. Each is compared with `==` against the text or digest master writes (the bit-identity
rule): the templates, a full-year network, and frames of inexact floats, NaN, ±inf, -0.0, mixed dtypes and
object columns. Every pinned literal was computed on master 5e8cbcdc, never by this branch."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import run_envelope, workflow_tools as W
from services.solver_service import SolverConfig
from tests.test_energization_per_period import MASTER_FP_EH_DATACENTER, _fp_ctx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TEMPLATES = sorted(
    p.name
    for p in (BACKEND / "project_templates").iterdir()
    if p.is_dir() and p.name.startswith("eh_")
)
STAMPS = pd.date_range("2030-01-01", periods=6, freq="h")
INEXACT = [0.187, 0.4, 0.1 + 0.2, np.nan, np.inf, -np.inf, -0.0, 0.0, 1e-300, 5e-324]
OTHER_NAN = struct.unpack("<d", struct.pack("<Q", 0x7FF8000000000001))[
    0
]  # a NaN of other bits than np.nan's

# Master 5e8cbcdc's digests (plain, exact): `edge_network`, and the eh_concentrator_staged 2027 stage (8760 h).
MASTER_FP_EDGE = (
    "a02c34a9864a9920f79781b1f3ff3385806e122f40b1a9dee73dfda12836854a",
    "d6b1e08f9698c4e580b3424bec3aa38376186d966841977897d7562d2c622cb9",
)
# The full year is built after a Link port 2 is registered (`full_year_ctx`), as in any process that built one.
MASTER_FP_FULL_YEAR = (
    "bdb0aeb4bb2ae36f9a4ef28aa5cc2d4df0235b4fca29208e418484940ddc51f1",
    "6232901daab6a01aa55b1b52181f43514808e4b762eeef9d2f229fc793ec5b39",
)
# Master 5e8cbcdc's sha256 of "\n".join(_exact_frame_json(f) for f in frames()).
MASTER_EXACT_TEXT = "4b92464b2bf24b18c82a0738f1f73b1cfddf5fe004525bea666d1e7fd55a06f5"


def lookalikes():
    """Pairs of indexes that differ in one thing `_exact_frame_json`'s index memo keys on."""
    periods = [2027, 2028]
    return {
        "names": (pd.Index(STAMPS, name="snapshot"), pd.Index(STAMPS, name="timestep")),
        "int vs float of the same bytes": (pd.Index([0, 0]), pd.Index([0.0, 0.0])),
        "-0.0 vs 0.0": (pd.Index([-0.0, 1.0]), pd.Index([0.0, 1.0])),
        "NaN payloads": (pd.Index([np.nan, 1.0]), pd.Index([OTHER_NAN, 1.0])),
        "datetime unit": (STAMPS, STAMPS.as_unit("s")),
        "multi level dtype": (
            pd.MultiIndex.from_product([periods, STAMPS[:3]]),
            pd.MultiIndex.from_product([[float(p) for p in periods], STAMPS[:3]]),
        ),
        "multi codes": (
            pd.MultiIndex.from_arrays([[2027, 2028], STAMPS[:2]]),
            pd.MultiIndex.from_arrays([[2028, 2027], STAMPS[:2]]).sort_values(),
        ),
        "multi names": (
            pd.MultiIndex.from_product(
                [periods, STAMPS[:3]], names=["period", "timestep"]
            ),
            pd.MultiIndex.from_product(
                [periods, STAMPS[:3]], names=["period", "snapshot"]
            ),
        ),
    }


def frames():
    """Frames whose text is pinned: every dtype path of `_exact_frame_json`, and the lookalike indexes."""
    periods = pd.MultiIndex.from_product(
        [[2027, 2028], STAMPS[:3]], names=["period", "timestep"]
    )
    out = [
        pd.DataFrame(
            {"x": INEXACT},
            index=pd.date_range("2030-01-01", periods=len(INEXACT), freq="h"),
        ),
        pd.DataFrame(
            {
                "mixed": pd.Series(
                    [1, 0.187, True, "s", None, pd.Timestamp("2030-01-01"), (1, 2.5)],
                    dtype=object,
                ),
                "i32": np.arange(7, dtype="int32"),
                "u8": np.arange(7, dtype="uint8"),
                "b": [True, False] * 3 + [True],
                "f32": np.float32([0.187] * 7),
            }
        ),
        pd.DataFrame({"a": np.arange(6.0)}, index=periods),
        pd.DataFrame(index=STAMPS.tz_localize("UTC")),
        pd.DataFrame(index=pd.DatetimeIndex([STAMPS[0], pd.NaT])),
        pd.DataFrame(
            index=pd.MultiIndex.from_arrays([[1, np.nan], [STAMPS[0], STAMPS[1]]])
        ),
        pd.DataFrame({"s": ["a", "b"]}, index=pd.Index(["x", "y"], name="name")),
        pd.DataFrame(),
        pd.DataFrame(columns=["c"], dtype=float),
        pd.DataFrame(index=STAMPS, columns=pd.RangeIndex(0)),
        pd.DataFrame(index=STAMPS, columns=pd.Index([], dtype=object)),
    ]
    for a, b in lookalikes().values():
        out += [pd.DataFrame(index=a), pd.DataFrame(index=b)]
    return out


def test_the_exact_text_is_masters():
    text = "\n".join(W._exact_frame_json(f) for f in frames())
    assert hashlib.sha256(text.encode()).hexdigest() == MASTER_EXACT_TEXT


def test_a_reused_index_text_is_the_text_of_that_index():
    """Every frame, read after every other one (and itself) in one memo, writes its own text."""
    alone = [W._exact_frame_json(f) for f in frames()]
    indexes = {}
    for _ in range(2):
        for f, text in zip(frames(), alone):
            assert W._exact_frame_json(f, indexes=indexes) == text
        for f, text in zip(reversed(frames()), reversed(alone)):
            assert W._exact_frame_json(f, indexes=indexes) == text


@pytest.mark.parametrize("pair", list(lookalikes()))
def test_indexes_that_differ_share_no_text(pair):
    a, b = lookalikes()[pair]
    indexes = {}
    texts = [
        W._exact_frame_json(
            pd.DataFrame({"v": np.arange(len(i))}, index=i), indexes=indexes
        )
        for i in (a, b)
    ]
    assert len(indexes) == 2
    assert texts == [
        W._exact_frame_json(pd.DataFrame({"v": np.arange(len(i))}, index=i))
        for i in (a, b)
    ]


def _pandas_json(frame, iso):
    try:
        return frame.to_json(date_format="iso") if iso else frame.to_json()
    except ValueError as exc:
        return exc


@pytest.mark.parametrize("iso", [False, True])
def test_a_frame_with_no_columns_writes_what_pandas_writes(iso):
    """The same text, or the same refusal (a repeated index label)."""
    repeated = [
        pd.DataFrame(index=pd.Index([0, 0])),
        pd.DataFrame(index=STAMPS[[0, 0]]),
    ]
    for f in (
        frames()
        + repeated
        + [pd.DataFrame(index=pd.MultiIndex.from_product([[2027], STAMPS]))]
    ):
        want = _pandas_json(f, iso)
        if isinstance(want, ValueError):
            with pytest.raises(ValueError, match=f"^{want}$"):
                W._plain_frame_json(f, iso)
        else:
            assert W._plain_frame_json(f, iso) == want


# ── the networks ─────────────────────────────────────────────────────────────

DC_SPEC = {
    "id": "eh_datacenter",
    "dc_load": {
        "spec": {
            "phases": [{"cod": "2030-01-01", "it_mw": 80}],
            "pue_at_design": 1.3,
            "redundancy": "N+1",
            "critical_share": 0.75,
            "curtailable_share": 0.2,
            "curtailment_price_eur_per_mwh": 7.0,
            "curtail_max_duration_h": 3.0,
        }
    },
}
# Every block `_input_digests` appends only when present, each reached by `edge_network`.
BLOCKS = [
    "split-storage/1",
    "envelope-floors/1",
    "energization/1",
    "fuel-curve/1",
    "composite-parts/StorageUnit/1",
    "composite-parts/Line/1",
    "pcs-statcom-stores/1",
    "pcs-statcom-links/1",
    "sizing-cuts-stores/1",
    "sizing-cuts-links/1",
    "dc-curtailment/1",
    "reserve-products/1",
    "operational-sizes/1",
]


def _ctx(n, config=None, user_ts=None, storage_dir=None):
    return types.SimpleNamespace(
        network=n,
        solver_state={"solver_config": config or SolverConfig()},
        storage_dir=storage_dir,
        user_ts=user_ts or {},
        network_revision=0,
    )


def edge_network(project_dir):
    """Inexact floats, NaN, ±inf, -0.0, an object column of mixed scalars, int32, investment periods, an
    uploaded series, and every optional block (`BLOCKS`): the split storage, envelope floors, energization,
    fuel curve, composite parts, PCS/STATCOM and sizing-cut tags, the DC curtailment terms (the saved DC spec),
    a reserve product with an attested storage (`derive_backup`) and an operational run's sizes."""
    from services.solver.sizing_cuts import tag_cuts, tag_gfm
    from services.solver.split_storage import add_split_storage

    (project_dir / "eh_template.json").write_text(json.dumps(DC_SPEC))
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], STAMPS[:3]]))
    n.investment_period_weightings.loc[:, "years"] = [10.0, 0.1]
    n.add("Bus", ["b0", "b1", "campus"], carrier="AC")
    n.add("Carrier", "gas", co2_emissions=0.187)
    n.add(
        "Generator",
        "g0",
        bus="b0",
        carrier="gas",
        p_nom=-0.0,
        marginal_cost=0.187,
        capital_cost=0.4,
        p_nom_extendable=True,
    )
    n.add(
        "Generator",
        "g1",
        bus="b1",
        carrier="gas",
        p_nom=np.inf,
        marginal_cost=0.1 + 0.2,
        p_nom_max=-np.inf,
    )
    n.add("Load", "l0", bus="b0", p_set=[0.187, np.nan, np.inf, -np.inf, -0.0, 1e-300])
    n.add("Line", "ln", bus0="b0", bus1="b1", x=0.4, r=1 / 3, s_nom=5, length=2.5)
    n.add("Link", "grid", bus0="b0", bus1="campus", p_nom=10.0)
    n.add("StorageUnit", "sb", bus="b0", p_nom=1.0, max_hours=4.0)
    add_split_storage(n, "bess", "campus", "high_c_bess")
    n.generators["p_nom_min"] = pd.Series(
        [1, True], index=n.generators.index, dtype=object
    )
    n.generators["build_year"] = n.generators["build_year"].astype("int32")
    n.generators_t.p_max_pu = pd.DataFrame(
        {"g0": [0.187, np.nan, 0.4, np.inf, -0.0, 0.3]}, index=n.snapshots
    )
    n.stores["smoothing_tier"] = 1.0
    n.generators["run_hours_cap"] = [100.0, np.nan]
    n.links_t["eh_energization_mw"] = pd.DataFrame(
        {"grid": [0.0, 0.187, 5.0, 5.0, np.nan, 10.0]}, index=n.snapshots
    )
    n.generators["fuel_price"] = [0.187, np.nan]
    n.storage_units["inv_power_overnight"] = 0.4
    n.lines["inv_per_km_overnight"] = 1 / 3
    n.stores["pcs_r_max"] = 2.0
    n.links["pq_x_pu"] = np.nan
    n.links.loc["grid", "pq_x_pu"] = 0.1
    tag_gfm(n, "bess", True)
    tag_cuts(n, "grid", {"import_cap_mw": 1000.0})
    n.meta["sizing_record"] = {"sha256": "abc"}
    config = SolverConfig(
        solve_strategy="rolling",
        fixed_sizes="capacity_run",
        commercial={
            "reserve_products": [
                {
                    "id": "fcr",
                    "kind": "fast_symmetric",
                    "direction": "symmetric",
                    "sustain_h": 0.25,
                    "capacity_price": None,
                }
            ],
            "reserve_no_backup_storages": ["sb"],
        },
    )
    return _ctx(
        n,
        config,
        {"load_x": pd.Series([0.187, np.nan, 2.0], index=STAMPS[:3])},
        project_dir,
    )


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
    """The 2027 stage. PyPSA registers a Link's port 2 process-wide once any network uses one, and the stage's
    `grid_import` (efficiency2 = -eff, no bus2) then reads efficiency2 1.0 instead of NaN: the built network,
    and so its fingerprint, depends on what the process built before (on master too). Registering the port first
    makes the build the same in every test order."""
    import eh_concentrator as C

    ports = pypsa.Network()
    ports.add("Bus", ["a", "b", "c"])
    ports.add("Link", "port 2", bus0="a", bus1="b", bus2="c")
    return _ctx(
        C.build_eh_concentrator_staged(T, years=(2027,)),
        SolverConfig(**T.solver_config("eh_concentrator_staged")),
    )


@pytest.fixture
def walks(monkeypatch):
    """The forms of each `_input_digests` walk, in order."""
    calls = []
    real = W._input_digests
    monkeypatch.setattr(
        W, "_input_digests", lambda ctx, forms: calls.append(forms) or real(ctx, forms)
    )
    return calls


def _pair_in_scope(ctx, order=(False, True)):
    with W.one_input_walk():
        got = {form: W._network_input_fingerprint(ctx, exact=form) for form in order}
    return got[False], got[True]


def test_the_edge_network_reaches_every_block(tmp_path, monkeypatch):
    texts = []
    real = hashlib.sha256
    monkeypatch.setattr(
        W,
        "hashlib",
        types.SimpleNamespace(sha256=lambda b: texts.append(b.decode()) or real(b)),
    )
    _pair_in_scope(edge_network(tmp_path))
    assert len(texts) == 2
    for text in texts:
        lines = text.split("\n")
        assert [b for b in BLOCKS if b not in lines] == [] and any(
            line.startswith("load_x") for line in lines
        )


@pytest.mark.parametrize("pinned", ["edge", "full_year", "eh_datacenter"])
def test_one_walk_gives_masters_digests(pinned, tmp_path, walks):
    ctx, master = {
        "edge": lambda: (edge_network(tmp_path), MASTER_FP_EDGE),
        "full_year": lambda: (full_year_ctx(), MASTER_FP_FULL_YEAR),
        "eh_datacenter": lambda: (
            _fp_ctx(T.build_eh_datacenter()),
            MASTER_FP_EH_DATACENTER,
        ),
    }[pinned]()
    assert (
        W._network_input_fingerprint(ctx),
        W._network_input_fingerprint(ctx, exact=True),
    ) == master
    assert walks == [(False,), (True,)]  # outside the scope: one form a walk
    walks.clear()
    for order in (
        (False, True),
        (True, False),
        (False, True),
    ):  # twice in a row: the walk writes nothing shared
        assert _pair_in_scope(ctx, order) == master
    assert walks == [(False, True)] * 3


@pytest.mark.parametrize("name", TEMPLATES)
def test_one_walk_gives_each_template_its_own_walks_digests(name, walks):
    ctx = template_ctx(name)
    single = (
        W._network_input_fingerprint(ctx),
        W._network_input_fingerprint(ctx, exact=True),
    )
    assert _pair_in_scope(ctx) == _pair_in_scope(ctx, (True, False)) == single


def test_an_index_of_objects_is_never_reused():
    """Object, tz-aware and mixed-level indexes key on nothing (their numpy bytes would be pointers)."""
    objects = [
        pd.Index(["a", "b"]),
        pd.Index([1, "b"], dtype=object),
        STAMPS.tz_localize("UTC"),
        pd.MultiIndex.from_arrays([["a", "b"], [1, 2]]),
        pd.MultiIndex.from_arrays([["c", "d"], [1, 2]]),
        pd.MultiIndex.from_arrays([STAMPS[:2].tz_localize("UTC"), [1, 2]]),
    ]
    indexes = {}
    texts = [
        W._exact_frame_json(pd.DataFrame(index=i), indexes=indexes) for i in objects
    ]
    assert indexes == {}
    assert texts == [W._exact_frame_json(pd.DataFrame(index=i)) for i in objects]
    assert texts[3] != texts[4]


def test_a_part_column_of_no_value_adds_no_block(tmp_path):
    """A composite part column that holds no value leaves the fingerprint as without the column (TCU 08)."""
    ctx = edge_network(tmp_path)
    ctx.network.storage_units.drop(columns="inv_power_overnight", inplace=True)
    without = _pair_in_scope(ctx)
    ctx.network.storage_units["inv_power_overnight"] = np.nan
    assert _pair_in_scope(ctx) == without


def test_the_walk_leaves_the_network_as_it_was(tmp_path):
    ctx = edge_network(tmp_path)
    n = ctx.network
    names, weightings = n.snapshots.names, n.snapshot_weightings.copy()
    frames_before = {
        c.name: (c.static.copy(), {k: f.copy() for k, f in c.dynamic.items()})
        for c in n.components
    }
    _pair_in_scope(ctx)
    assert n.snapshots.names == names and n.snapshot_weightings.equals(weightings)
    for c in n.components:
        static, dynamic = frames_before[c.name]
        pd.testing.assert_frame_equal(c.static, static)
        assert c.static.index.names == static.index.names
        for k, f in dynamic.items():
            pd.testing.assert_frame_equal(c.dynamic[k], f)
            assert c.dynamic[k].index.names == f.index.names


def test_identity_reads_both_forms_from_one_walk(tmp_path, walks):
    ctx = edge_network(tmp_path)
    found = run_envelope.identity(ctx)
    assert (found["fingerprint"], found["exact"]) == MASTER_FP_EDGE and walks == [
        (False, True)
    ]
    walks.clear()
    found = run_envelope.identity(ctx, exact=False)
    assert (found["fingerprint"], found["exact"]) == (
        MASTER_FP_EDGE[0],
        None,
    ) and walks == [(False,)]


# ── what the scope keeps, and for whom ──────────────────────────────────────


def test_the_kept_form_is_served_once(tmp_path, walks):
    ctx = edge_network(tmp_path)
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        assert (
            W._network_input_fingerprint(ctx, exact=True) == MASTER_FP_EDGE[1]
        )  # kept
        assert (
            W._network_input_fingerprint(ctx, exact=True) == MASTER_FP_EDGE[1]
        )  # a third read walks again
    assert walks == [(False, True)] * 2
    walks.clear()
    with (
        W.one_input_walk()
    ):  # the kept form is the other one: never served for the same form again
        assert W._network_input_fingerprint(ctx) == MASTER_FP_EDGE[0]
        assert W._network_input_fingerprint(ctx) == MASTER_FP_EDGE[0]
    assert walks == [(False, True)] * 2


def test_another_context_or_network_walks_its_own(tmp_path, walks):
    ctx = edge_network(tmp_path)
    other = types.SimpleNamespace(**vars(ctx))
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        assert (
            W._network_input_fingerprint(other, exact=True) == MASTER_FP_EDGE[1]
        )  # another context
        ctx.network = ctx.network.copy()  # another network object on the same context
        assert W._network_input_fingerprint(ctx, exact=True) == MASTER_FP_EDGE[1]
    assert walks == [(False, True)] * 3


def test_a_reshaped_network_between_the_reads_walks_again(tmp_path, walks):
    ctx = edge_network(tmp_path)
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        ctx.network.add(
            "Bus", "b9"
        )  # a test-only abuse of the no-write precondition: the guard sees it
        moved = W._network_input_fingerprint(ctx, exact=True)
    assert (
        walks == [(False, True)] * 2
        and moved == W._network_input_fingerprint(ctx, exact=True) != MASTER_FP_EDGE[1]
    )


def test_an_in_place_value_write_between_the_reads_is_not_seen(tmp_path):
    """The precondition, said as a test: the two reads in a scope see no write between them. A value written in
    place between them (same objects, same shapes) is the one change the guard does not see: the kept form is
    the one from before the write."""
    ctx = edge_network(tmp_path)
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        ctx.network.generators.loc["g0", "marginal_cost"] = 0.188
        stale = W._network_input_fingerprint(ctx, exact=True)
    assert stale == MASTER_FP_EDGE[1] != W._network_input_fingerprint(ctx, exact=True)


def test_a_scope_ends_with_its_block_even_on_an_error(tmp_path, walks):
    ctx = edge_network(tmp_path)
    with pytest.raises(RuntimeError), W.one_input_walk():
        W._network_input_fingerprint(ctx)
        raise RuntimeError
    assert W._ONE_WALK.get() is None
    W._network_input_fingerprint(ctx, exact=True)
    assert walks == [(False, True), (True,)]


def test_nested_scopes_and_threads_keep_their_own(tmp_path, walks):
    ctx = edge_network(tmp_path)
    with W.one_input_walk():
        W._network_input_fingerprint(ctx)
        with W.one_input_walk():  # the inner scope sees nothing the outer keeps
            W._network_input_fingerprint(ctx, exact=True)
        assert walks == [(False, True)] * 2
        seen = []
        thread = threading.Thread(
            target=lambda: seen.append(
                (W._ONE_WALK.get(), W._network_input_fingerprint(ctx, exact=True))
            )
        )
        thread.start()
        thread.join()
        assert seen == [(None, MASTER_FP_EDGE[1])] and walks[-1] == (
            True,
        )  # another thread: no scope
        assert (
            W._network_input_fingerprint(ctx, exact=True) == MASTER_FP_EDGE[1]
        )  # still kept here
    assert walks == [(False, True), (False, True), (True,)]


# ── a form that fails ────────────────────────────────────────────────────────


def test_an_exact_form_that_refuses_leaves_the_plain_form_as_it_was(tmp_path):
    ctx = edge_network(tmp_path)
    ctx.network.generators["p_nom_min"] = pd.Series(
        [{"a": 1}, 1], index=ctx.network.generators.index, dtype=object
    )
    plain = W._network_input_fingerprint(ctx)
    with pytest.raises(Exception) as alone:
        W._network_input_fingerprint(ctx, exact=True)
    with W.one_input_walk():
        assert W._network_input_fingerprint(ctx) == plain
        with pytest.raises(type(alone.value)) as scoped:
            W._network_input_fingerprint(ctx, exact=True)
    assert str(scoped.value) == str(alone.value)
    found = run_envelope.identity(ctx)
    assert (found["fingerprint"], found["exact"]) == (plain, None)


def test_a_plain_form_that_fails_leaves_the_exact_form_as_it_was(tmp_path, monkeypatch):
    ctx = edge_network(tmp_path)

    def broken(frame, iso=False):
        raise ValueError("plain broke")

    monkeypatch.setattr(W, "_plain_frame_json", broken)
    with W.one_input_walk():
        with pytest.raises(ValueError, match="^plain broke$"):
            W._network_input_fingerprint(ctx)
        assert W._network_input_fingerprint(ctx, exact=True) == MASTER_FP_EDGE[1]
    found = run_envelope.identity(ctx)
    assert (found["fingerprint"], found["exact"]) == (None, MASTER_FP_EDGE[1])
