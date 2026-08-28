# gridspine Increment 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 39-bus vertical slice of the planning-to-stability pipeline: PyPSA nodal UC dispatch → dispatch table → pandapower AC load flow → PSS/E .raw v33 export → PowerFactory <1% validation harness.

**Architecture:** New headless package `gridspine/` beside `scripts/`. Stages communicate only via validated artifacts (dataframes/files); engine imports are caged (`pypsa` only in `producers/`, `pandapower` only in `ingest/`, `static/`, `handoff/`). Canonical element IDs are the detailed grid's names; PyPSA runs nodal so the region map is identity.

**Tech Stack:** Python 3.12 (pixi default env), pypsa==1.1.2, highspy==1.14.0, pandas>=2.1, pandapower (new dep), pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-gridspine-design.md`

## Global Constraints

- ALL commands via `pixi run …` — bare `python`/`pytest` is the wrong env (exit 127 / wrong deps).
- Execute in a dedicated worktree (superpowers:using-git-worktrees) — 20+ concurrent sessions share this repo. `pixi install` provisions the worktree's env on first use.
- Commits are path-limited: `git add <named files>` then `git commit -- <same paths>`. Before each commit run `git commit --dry-run -- <paths>` and check NO untracked file you created is missing from the list (path-limited commits silently omit untracked files).
- Engine-import cage: `import pypsa` ONLY under `gridspine/producers/`; `import pandapower` ONLY under `gridspine/ingest/`, `gridspine/static/`, `gridspine/handoff/`. `gridspine/schema/`, `gridspine/ranking/`, `gridspine/readback/` import neither.
- Canonical IDs: element NAMES (strings) cross stage boundaries; positional indexes never do.
- Every task report carries **TDD Evidence**: RED command + failing output, GREEN command + passing output.
- Test command for this package: `pixi run gridspine-tests` (defined in Task 1).
- Model routing: tasks marked **[Opus]** go to an Opus subagent; **[Fable review]** means Opus implements but the master (Fable) reviews the diff line-by-line before accepting; **[FABLE]** means implement with a Fable subagent or inline.
- If a probe step reveals an API/count differing from the plan's constants, update the constant in the SAME task and note it in the report — do not silently code around it.

---

### Task 1: Package skeleton, pixi wiring, ContractError **[Opus]**

**Files:**
- Modify: `pixi.toml` (add `pandapower` to `[dependencies]`, add `gridspine-tests` to `[tasks]`)
- Create: `gridspine/__init__.py`, `gridspine/schema/__init__.py`, `gridspine/ingest/__init__.py`, `gridspine/producers/__init__.py`, `gridspine/static/__init__.py`, `gridspine/handoff/__init__.py`, `gridspine/readback/__init__.py`, `gridspine/drivers/__init__.py`
- Create: `gridspine/schema/contracts.py`
- Test: `tests/gridspine/__init__.py`, `tests/gridspine/test_contracts.py`

**Interfaces:**
- Produces: `gridspine.schema.contracts.ContractError(Exception)` — raised by every validator in later tasks. `pixi run gridspine-tests` as the suite gate.

- [ ] **Step 1: Add dependency + task to pixi.toml**

In `[dependencies]` (alphabetical position, after `pandas`):
```toml
pandapower = ">=3.0"
```
In `[tasks]`:
```toml
gridspine-tests = "python -m pytest tests/gridspine -v"
```
Run: `pixi install` (updates `pixi.lock` — commit both files in this task; they are shared, so commit promptly).

- [ ] **Step 2: Write the failing test**

`tests/gridspine/test_contracts.py`:
```python
from gridspine.schema.contracts import ContractError


def test_contract_error_is_exception():
    assert issubclass(ContractError, Exception)


def test_pandapower_importable():
    import pandapower  # noqa: F401  — env wiring check (test code, not an engine-cage violation)
```
Also create empty `tests/gridspine/__init__.py`.

- [ ] **Step 3: Run test to verify it fails**

Run: `pixi run gridspine-tests`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'gridspine'`

- [ ] **Step 4: Create package dirs + contracts.py**

All eight `__init__.py` files empty. `gridspine/schema/contracts.py`:
```python
"""Stage-boundary contracts. Every artifact crossing a gridspine stage
boundary is validated here; stages never import each other's internals."""


class ContractError(ValueError):
    """An artifact violates its stage-boundary contract."""
```
If collection can't import `gridspine`, add `tests/gridspine/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pixi run gridspine-tests`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add pixi.toml pixi.lock gridspine tests/gridspine
git commit --dry-run -- pixi.toml pixi.lock gridspine tests/gridspine
git commit -m "feat(gridspine): package skeleton, pixi wiring, ContractError" -- pixi.toml pixi.lock gridspine tests/gridspine
```

---

### Task 2: DispatchTable contract **[Opus, Fable review]**

**Files:**
- Create: `gridspine/schema/dispatch.py`
- Test: `tests/gridspine/test_dispatch_contract.py`

**Interfaces:**
- Consumes: `ContractError` from Task 1.
- Produces: `validate_dispatch(df: pd.DataFrame) -> pd.DataFrame` (returns the validated frame, dtypes normalised) and constant `DISPATCH_COLUMNS: dict[str, str]`. Columns: `unit_id` (str), `hour` (int), `p_mw` (float), `q_mvar` (float), `status` (int 0/1).

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_dispatch_contract.py`:
```python
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch


def good():
    return pd.DataFrame({
        "unit_id": ["G_A", "G_B", "G_A", "G_B"],
        "hour": [0, 0, 1, 1],
        "p_mw": [100.0, 50.0, 0.0, 80.0],
        "q_mvar": [0.0, 0.0, 0.0, 0.0],
        "status": [1, 1, 0, 1],
    })


def test_valid_table_passes_and_normalises_dtypes():
    out = validate_dispatch(good())
    assert out["status"].dtype == "int64"
    assert out["p_mw"].dtype == "float64"


def test_missing_column_rejected():
    with pytest.raises(ContractError, match="q_mvar"):
        validate_dispatch(good().drop(columns=["q_mvar"]))


def test_bad_status_value_rejected():
    df = good()
    df.loc[0, "status"] = 2
    with pytest.raises(ContractError, match="status"):
        validate_dispatch(df)


def test_duplicate_unit_hour_rejected():
    df = pd.concat([good(), good().iloc[[0]]])
    with pytest.raises(ContractError, match="duplicate"):
        validate_dispatch(df)


def test_offline_unit_with_nonzero_p_rejected():
    df = good()
    df.loc[2, "p_mw"] = 25.0  # status 0 but producing
    with pytest.raises(ContractError, match="status 0"):
        validate_dispatch(df)


def test_nan_p_rejected():
    df = good()
    df.loc[1, "p_mw"] = float("nan")
    with pytest.raises(ContractError, match="NaN"):
        validate_dispatch(df)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.schema.dispatch'`

- [ ] **Step 3: Implement**

`gridspine/schema/dispatch.py`:
```python
"""The stage-1 -> stage-2 contract: per-unit, per-hour dispatch keyed by
canonical unit_id. PyPSA is one producer of this table; client-supplied
snapshots are another. Downstream stages never see a PyPSA object."""
import pandas as pd

from .contracts import ContractError

DISPATCH_COLUMNS = {
    "unit_id": "object",
    "hour": "int64",
    "p_mw": "float64",
    "q_mvar": "float64",
    "status": "int64",
}
_P_OFFLINE_TOL_MW = 1e-4


def validate_dispatch(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(DISPATCH_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"dispatch table missing columns: {sorted(missing)}")
    out = df.copy()
    for col in ("p_mw", "q_mvar"):
        if out[col].isna().any():
            raise ContractError(f"dispatch table has NaN in {col}")
    try:
        out = out.astype(DISPATCH_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"dispatch table dtype coercion failed: {exc}") from exc
    if not out["status"].isin([0, 1]).all():
        bad = sorted(out.loc[~out["status"].isin([0, 1]), "status"].unique())
        raise ContractError(f"status must be 0/1, got {bad}")
    dup = out.duplicated(subset=["unit_id", "hour"])
    if dup.any():
        raise ContractError(f"duplicate (unit_id, hour) rows: {out.loc[dup, 'unit_id'].tolist()}")
    offline_producing = (out["status"] == 0) & (out["p_mw"].abs() > _P_OFFLINE_TOL_MW)
    if offline_producing.any():
        raise ContractError(
            f"units with status 0 but nonzero p_mw: {out.loc[offline_producing, 'unit_id'].tolist()}"
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass (Task 1's 2 + these 6)

- [ ] **Step 5: Mutation check (repo rule: break the code, prove the guard test fails)**

Temporarily set `_P_OFFLINE_TOL_MW = 1e9`; run suite; `test_offline_unit_with_nonzero_p_rejected` MUST fail. Revert. Record both outputs in the report.

- [ ] **Step 6: Commit**

```bash
git add gridspine/schema/dispatch.py tests/gridspine/test_dispatch_contract.py
git commit --dry-run -- gridspine/schema/dispatch.py tests/gridspine/test_dispatch_contract.py
git commit -m "feat(gridspine): DispatchTable stage-boundary contract" -- gridspine/schema/dispatch.py tests/gridspine/test_dispatch_contract.py
```

---

### Task 3: Canonical network registry **[Opus, Fable review]**

**Files:**
- Create: `gridspine/schema/network.py`
- Test: `tests/gridspine/test_network_registry.py`

**Interfaces:**
- Consumes: `ContractError`.
- Produces:
  - `validate_canonical(buses: pd.Series, unit_names: pd.Series) -> None` — engine-free ID rules: non-null, unique, string, 1..12 chars (PSS/E v33 NAME field limit — fail loud now, not at export).
  - `unit_registry(gen_names, gen_buses, ext_names, ext_buses) -> pd.DataFrame` indexed by `unit_id` with columns `bus` (str), `kind` ('gen'|'ext_grid'). Engine-free: takes plain sequences, so `schema/` imports no engine.

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_network_registry.py`:
```python
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.network import unit_registry, validate_canonical


def test_valid_ids_pass():
    validate_canonical(
        buses=pd.Series(["BUS_01", "BUS_02"]),
        unit_names=pd.Series(["G_BUS_01"]),
    )


def test_duplicate_bus_name_rejected():
    with pytest.raises(ContractError, match="duplicate"):
        validate_canonical(pd.Series(["B1", "B1"]), pd.Series(["G1"]))


def test_null_name_rejected():
    with pytest.raises(ContractError, match="null"):
        validate_canonical(pd.Series(["B1", None]), pd.Series(["G1"]))


def test_name_longer_than_12_chars_rejected():
    with pytest.raises(ContractError, match="12"):
        validate_canonical(pd.Series(["THIRTEEN_CHAR"]), pd.Series(["G1"]))


def test_unit_name_colliding_with_other_unit_rejected():
    with pytest.raises(ContractError, match="duplicate"):
        validate_canonical(pd.Series(["B1"]), pd.Series(["G1", "G1"]))


def test_unit_registry_merges_gen_and_ext_grid():
    reg = unit_registry(
        gen_names=pd.Series(["G_B1"]), gen_buses=pd.Series(["B1"]),
        ext_names=pd.Series(["SLK_B2"]), ext_buses=pd.Series(["B2"]),
    )
    assert reg.loc["G_B1", "kind"] == "gen"
    assert reg.loc["SLK_B2", "kind"] == "ext_grid"
    assert reg.loc["SLK_B2", "bus"] == "B2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.schema.network'`

- [ ] **Step 3: Implement**

`gridspine/schema/network.py`:
```python
"""Canonical-ID rules. Names are the keys that cross every stage boundary:
PyPSA bus name == pandapower bus name == .raw NAME == PowerFactory name.
The 12-char cap is the PSS/E v33 NAME field width — enforced here so an
illegal ID fails at ingest, not at export."""
import pandas as pd

from .contracts import ContractError

MAX_NAME_LEN = 12


def _check_series(s: pd.Series, what: str) -> None:
    if s.isna().any():
        raise ContractError(f"{what} contains null names")
    if not all(isinstance(v, str) for v in s):
        raise ContractError(f"{what} contains non-string names")
    if s.duplicated().any():
        raise ContractError(f"{what} contains duplicate names: {sorted(s[s.duplicated()].unique())}")
    too_long = s[s.str.len() > MAX_NAME_LEN]
    if len(too_long):
        raise ContractError(
            f"{what} names exceed {MAX_NAME_LEN} chars (PSS/E v33 NAME limit): {sorted(too_long)}"
        )
    if (s.str.len() == 0).any():
        raise ContractError(f"{what} contains empty names")


def validate_canonical(buses: pd.Series, unit_names: pd.Series) -> None:
    _check_series(buses.reset_index(drop=True), "buses")
    _check_series(unit_names.reset_index(drop=True), "units")


def unit_registry(gen_names, gen_buses, ext_names, ext_buses) -> pd.DataFrame:
    gens = pd.DataFrame({"unit_id": list(gen_names), "bus": list(gen_buses), "kind": "gen"})
    exts = pd.DataFrame({"unit_id": list(ext_names), "bus": list(ext_buses), "kind": "ext_grid"})
    reg = pd.concat([gens, exts], ignore_index=True).set_index("unit_id")
    if reg.index.duplicated().any():
        raise ContractError(f"duplicate unit ids: {sorted(reg.index[reg.index.duplicated()])}")
    return reg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add gridspine/schema/network.py tests/gridspine/test_network_registry.py
git commit --dry-run -- gridspine/schema/network.py tests/gridspine/test_network_registry.py
git commit -m "feat(gridspine): canonical-ID rules and unit registry" -- gridspine/schema/network.py tests/gridspine/test_network_registry.py
```

---

### Task 4: Ingest — case39 canonical fixture **[Opus]**

**Files:**
- Create: `gridspine/ingest/pandapower_source.py`
- Test: `tests/gridspine/test_case39_ingest.py`

**Interfaces:**
- Consumes: `validate_canonical`, `unit_registry` (Task 3).
- Produces: `load_case39() -> pandapowerNet` — case39 with canonical names (`BUS_01`..`BUS_39` in original index order; gens `G_<busname>`; ext_grid `SLK_<busname>`), validated. `registry_from_net(net) -> pd.DataFrame` (wraps `unit_registry`).

- [ ] **Step 1: Probe the fixture shape (record output in report)**

Run: `pixi run python -c "import pandapower.networks as pn; n = pn.case39(); print('bus', len(n.bus), 'gen', len(n.gen), 'ext', len(n.ext_grid), 'line', len(n.line), 'trafo', len(n.trafo))"`
Expected (adjust test constants if installed pandapower differs): `bus 39 gen 9 ext 1 line 34 trafo 12`

- [ ] **Step 2: Write the failing tests**

`tests/gridspine/test_case39_ingest.py`:
```python
import pandapower as pp

from gridspine.ingest.pandapower_source import load_case39, registry_from_net


def test_case39_has_canonical_names():
    net = load_case39()
    assert len(net.bus) == 39
    assert list(net.bus["name"])[:2] == ["BUS_01", "BUS_02"]
    assert net.bus["name"].is_unique
    assert (len(net.gen) + len(net.ext_grid)) == 10


def test_registry_covers_all_units():
    net = load_case39()
    reg = registry_from_net(net)
    assert len(reg) == 10
    assert (reg["kind"] == "ext_grid").sum() == 1
    assert set(reg["bus"]).issubset(set(net.bus["name"]))


def test_case39_load_flow_converges():
    net = load_case39()
    pp.runpp(net)
    assert net.converged
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.ingest.pandapower_source'`

- [ ] **Step 4: Implement**

`gridspine/ingest/pandapower_source.py`:
```python
"""Stage 0 (minimal): reference networks with canonical IDs assigned.
Allowed to import pandapower (with static/ and handoff/)."""
import pandas as pd
import pandapower.networks as pn

from gridspine.schema.network import validate_canonical
from gridspine.schema.network import unit_registry as _unit_registry


def load_case39():
    net = pn.case39()
    net.bus["name"] = [f"BUS_{i + 1:02d}" for i in range(len(net.bus))]
    bus_name = net.bus["name"]
    net.gen["name"] = [f"G_{bus_name.at[b]}" for b in net.gen["bus"]]
    net.ext_grid["name"] = [f"SLK_{bus_name.at[b]}" for b in net.ext_grid["bus"]]
    unit_names = list(net.gen["name"]) + list(net.ext_grid["name"])
    validate_canonical(net.bus["name"], pd.Series(unit_names))
    return net


def registry_from_net(net):
    bus_name = net.bus["name"]
    return _unit_registry(
        gen_names=net.gen["name"],
        gen_buses=[bus_name.at[b] for b in net.gen["bus"]],
        ext_names=net.ext_grid["name"],
        ext_buses=[bus_name.at[b] for b in net.ext_grid["bus"]],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add gridspine/ingest/pandapower_source.py tests/gridspine/test_case39_ingest.py
git commit --dry-run -- gridspine/ingest/pandapower_source.py tests/gridspine/test_case39_ingest.py
git commit -m "feat(gridspine): case39 ingest with canonical IDs" -- gridspine/ingest/pandapower_source.py tests/gridspine/test_case39_ingest.py
```

---

### Task 5: Producer — detailed-grid → PyPSA converter **[Opus]**

**Files:**
- Create: `gridspine/producers/pypsa_nodal.py` (converter half)
- Test: `tests/gridspine/test_to_pypsa.py`

**Interfaces:**
- Consumes: a pandapower net with canonical names (Task 4). This module receives the net OBJECT but imports only `pypsa` — it reads the net's plain DataFrames (`net.bus`, `net.line`, …), never calls pandapower functions.
- Produces: `to_pypsa(net, snapshots: int = 24) -> pypsa.Network` — nodal 1:1: PyPSA bus names == pandapower bus names. Lines → `Line` (r/x in ohm, `s_nom` from `max_i_ka`); trafos → `Transformer` (`x ≈ vk_percent/100`, `r = vkr_percent/100`, `s_nom = sn_mva`); loads per bus with fixed 24-h `LOAD_SHAPE`; gens committable (`p_min_pu=0.3`, `min_up_time=2`, `min_down_time=2`), `p_nom = max_p_mw` (fallback `1.2 × p_mw`), staggered `marginal_cost = 10 + 4·i`; ext_grid → non-committable generator `p_nom = 3000`, `marginal_cost = 80` (ledgered assumption).

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_to_pypsa.py`:
```python
from gridspine.ingest.pandapower_source import load_case39
from gridspine.producers.pypsa_nodal import LOAD_SHAPE, to_pypsa


def test_identity_mapping_bus_names():
    net = load_case39()
    n = to_pypsa(net)
    assert set(n.buses.index) == set(net.bus["name"])


def test_all_units_present_and_committable_flags():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.generators) == 10
    assert int(n.generators["committable"].sum()) == 9  # ext_grid unit is not
    slack_units = [u for u in n.generators.index if u.startswith("SLK_")]
    assert len(slack_units) == 1
    assert not n.generators.loc[slack_units[0], "committable"]


def test_snapshots_and_load_shape():
    net = load_case39()
    n = to_pypsa(net, snapshots=24)
    assert len(n.snapshots) == 24
    assert len(LOAD_SHAPE) == 24
    total_p = float(net.load["p_mw"].sum())
    peak = float(n.loads_t.p_set.sum(axis=1).max())
    assert abs(peak - total_p * max(LOAD_SHAPE)) < 1.0


def test_branch_counts():
    net = load_case39()
    n = to_pypsa(net)
    assert len(n.lines) == len(net.line)
    assert len(n.transformers) == len(net.trafo)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.producers.pypsa_nodal'`

- [ ] **Step 3: Implement**

`gridspine/producers/pypsa_nodal.py` (converter half; Task 6 appends the dispatch half):
```python
"""Detailed-grid -> PyPSA nodal converter + UC dispatch producer.
Nodal = identity region map: every PyPSA element name equals the canonical
detailed-grid name. The only module allowed to import pypsa."""
import numpy as np
import pandas as pd
import pypsa

# Normalised daily load shape (24 h), peak = 1.0 at hour 19. Ledgered
# assumption: synthetic shape for the vertical slice; real studies supply
# measured series.
LOAD_SHAPE = [
    0.62, 0.58, 0.56, 0.45, 0.56, 0.60, 0.68, 0.78, 0.86, 0.90, 0.92, 0.93,
    0.92, 0.90, 0.89, 0.90, 0.93, 0.97, 1.00, 0.99, 0.94, 0.86, 0.76, 0.67,
]

EXT_GRID_P_NOM_MW = 3000.0
EXT_GRID_MARGINAL_COST = 80.0  # EUR/MWh — import priced above all thermal units


def to_pypsa(net, snapshots: int = 24) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(snapshots))
    bus_name = net.bus["name"]

    for _, b in net.bus.iterrows():
        n.add("Bus", b["name"], v_nom=b["vn_kv"])

    for i, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        n.add(
            "Line", f"L_{i:02d}",
            bus0=bus_name.at[ln["from_bus"]], bus1=bus_name.at[ln["to_bus"]],
            r=ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            x=ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            s_nom=np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"],
        )

    for i, tr in net.trafo.iterrows():
        n.add(
            "Transformer", f"T_{i:02d}",
            bus0=bus_name.at[tr["hv_bus"]], bus1=bus_name.at[tr["lv_bus"]],
            s_nom=tr["sn_mva"], x=tr["vk_percent"] / 100.0,
            r=tr["vkr_percent"] / 100.0, tap_ratio=1.0, model="t",
        )

    per_bus = net.load.groupby("bus")["p_mw"].sum()
    shape = pd.Series(LOAD_SHAPE[:snapshots], index=n.snapshots)
    for b, p in per_bus.items():
        n.add("Load", f"LD_{bus_name.at[b]}", bus=bus_name.at[b], p_set=shape * float(p))

    for i, (_, g) in enumerate(net.gen.iterrows()):
        p_nom = g.get("max_p_mw", np.nan)
        if not np.isfinite(p_nom) or p_nom <= 0:
            p_nom = 1.2 * g["p_mw"]
        n.add(
            "Generator", g["name"], bus=bus_name.at[g["bus"]],
            p_nom=float(p_nom), committable=True, p_min_pu=0.3,
            min_up_time=2, min_down_time=2, start_up_cost=1000.0,
            marginal_cost=10.0 + 4.0 * i,
        )

    for _, e in net.ext_grid.iterrows():
        n.add("Generator", e["name"], bus=bus_name.at[e["bus"]],
              p_nom=EXT_GRID_P_NOM_MW, committable=False,
              marginal_cost=EXT_GRID_MARGINAL_COST)
    return n
```
Note `LOAD_SHAPE[3] = 0.45` — the valley is deliberately deep enough that UC turns at least one unit off (Task 6's commitment test depends on it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass. If a pandapower column differs (`max_p_mw` absent, `parallel` missing), record the probe output, adjust, note in report.

- [ ] **Step 5: Commit**

```bash
git add gridspine/producers/pypsa_nodal.py tests/gridspine/test_to_pypsa.py
git commit --dry-run -- gridspine/producers/pypsa_nodal.py tests/gridspine/test_to_pypsa.py
git commit -m "feat(gridspine): detailed-grid to PyPSA nodal converter" -- gridspine/producers/pypsa_nodal.py tests/gridspine/test_to_pypsa.py
```

---

### Task 6: Producer — UC dispatch → DispatchTable **[Opus]**

**Files:**
- Modify: `gridspine/producers/pypsa_nodal.py` (append dispatch half)
- Test: `tests/gridspine/test_dispatch_producer.py`

**Interfaces:**
- Consumes: `to_pypsa` (Task 5), `validate_dispatch` (Task 2).
- Produces: `run_uc(n) -> pypsa.Network` (highs MILP; raises `RuntimeError` on non-optimal) and `to_dispatch_table(n) -> pd.DataFrame` — validated table. `q_mvar = 0.0` for all rows: gens enter the LF as PV nodes, Q is a load-flow RESULT (ledgered assumption). Non-committable units: `status = 1` iff `p_mw > 1e-4`.

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_dispatch_producer.py`:
```python
import pytest

from gridspine.ingest.pandapower_source import load_case39
from gridspine.producers.pypsa_nodal import run_uc, to_dispatch_table, to_pypsa
from gridspine.schema.dispatch import validate_dispatch


@pytest.fixture(scope="module")
def solved():
    n = to_pypsa(load_case39(), snapshots=24)
    return run_uc(n)


def test_dispatch_table_validates(solved):
    table = to_dispatch_table(solved)
    validate_dispatch(table)  # raises on violation
    assert len(table) == 10 * 24


def test_energy_balance_per_hour(solved):
    table = to_dispatch_table(solved)
    gen_h0 = table[table["hour"] == 0]["p_mw"].sum()
    load_h0 = float(solved.loads_t.p_set.iloc[0].sum())
    assert abs(gen_h0 - load_h0) / load_h0 < 0.01


def test_status_is_binary_commitment_not_prorata(solved):
    table = to_dispatch_table(solved)
    # In the load valley at least one committable unit must be OFF — this
    # binary on/off is the metric the pipeline exists to preserve (min-
    # inertia hours need real UC, not scaled-down everything-online).
    valley = table[table["hour"] == 3]
    assert (valley["status"] == 0).any()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ImportError: cannot import name 'run_uc'`

- [ ] **Step 3: Implement (append to pypsa_nodal.py)**

```python
_STATUS_P_TOL_MW = 1e-4


def run_uc(n: pypsa.Network) -> pypsa.Network:
    status, condition = n.optimize(solver_name="highs")
    if condition != "optimal":
        raise RuntimeError(f"UC solve not optimal: {status}/{condition}")
    return n


def to_dispatch_table(n: pypsa.Network) -> pd.DataFrame:
    from gridspine.schema.dispatch import validate_dispatch

    rows = []
    p = n.generators_t.p
    committable = n.generators["committable"]
    status_t = getattr(n.generators_t, "status", pd.DataFrame())
    for hour, snap in enumerate(n.snapshots):
        for unit in n.generators.index:
            p_mw = float(p.at[snap, unit])
            if committable.at[unit] and unit in status_t.columns:
                st = int(round(float(status_t.at[snap, unit])))
            else:
                st = 1 if abs(p_mw) > _STATUS_P_TOL_MW else 0
            if st == 0:
                p_mw = 0.0  # zero out solver residuals below tolerance
            rows.append({"unit_id": unit, "hour": hour, "p_mw": p_mw,
                         "q_mvar": 0.0, "status": st})
    return validate_dispatch(pd.DataFrame(rows))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass (MILP ~seconds). If the commitment test fails because no unit is ever off, the valley isn't deep enough relative to `p_min_pu` — deepen `LOAD_SHAPE` hour 3 further (0.40) and note in report.

- [ ] **Step 5: Commit**

```bash
git add gridspine/producers/pypsa_nodal.py tests/gridspine/test_dispatch_producer.py
git commit --dry-run -- gridspine/producers/pypsa_nodal.py tests/gridspine/test_dispatch_producer.py
git commit -m "feat(gridspine): UC dispatch producer emitting DispatchTable" -- gridspine/producers/pypsa_nodal.py tests/gridspine/test_dispatch_producer.py
```

---

### Task 7: Static — apply dispatch + AC load flow **[Opus]**

**Files:**
- Create: `gridspine/static/loadflow.py`
- Test: `tests/gridspine/test_loadflow.py`

**Interfaces:**
- Consumes: validated dispatch table (Task 2 shape), canonical net + registry (Task 4).
- Produces:
  - `apply_dispatch(net, table, hour, registry) -> None` — mutates net: `kind == 'gen'` rows set `net.gen.p_mw` + `net.gen.in_service` by canonical name; `ext_grid` rows skipped (slack absorbs residual).
  - `run_lf(net) -> LFResult` — dataclass: `converged: bool`, `bus: pd.DataFrame` (index canonical bus name; columns `vm_pu`, `va_degree`), `branch_loading: pd.DataFrame` (index `L_xx`/`T_xx`; column `loading_percent`), `slack_p_mw: float`. Non-convergence returns `LFResult(converged=False)` — a result, not an exception.

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_loadflow.py`:
```python
import pandas as pd

from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.static.loadflow import LFResult, apply_dispatch, run_lf


def dispatch_all_on(net, registry):
    rows = []
    for unit_id, rec in registry.iterrows():
        if rec["kind"] == "gen":
            i = net.gen.index[net.gen["name"] == unit_id][0]
            p = float(net.gen.at[i, "p_mw"])
        else:
            p = 0.0
        rows.append({"unit_id": unit_id, "hour": 0, "p_mw": p, "q_mvar": 0.0, "status": 1})
    return pd.DataFrame(rows)


def test_lf_converges_with_native_dispatch():
    net = load_case39()
    reg = registry_from_net(net)
    apply_dispatch(net, dispatch_all_on(net, reg), hour=0, registry=reg)
    res = run_lf(net)
    assert isinstance(res, LFResult) and res.converged
    assert set(res.bus.index) == set(net.bus["name"])
    assert res.bus["vm_pu"].between(0.8, 1.2).all()


def test_offline_unit_is_out_of_service():
    net = load_case39()
    reg = registry_from_net(net)
    table = dispatch_all_on(net, reg)
    victim = table.loc[table["unit_id"].str.startswith("G_"), "unit_id"].iloc[0]
    table.loc[table["unit_id"] == victim, ["status", "p_mw"]] = [0, 0.0]
    apply_dispatch(net, table, hour=0, registry=reg)
    i = net.gen.index[net.gen["name"] == victim][0]
    assert not bool(net.gen.at[i, "in_service"])


def test_nonconvergence_is_a_result_not_a_crash():
    net = load_case39()
    net.load["p_mw"] *= 25.0  # absurd loading
    res = run_lf(net)
    assert res.converged is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.static.loadflow'`

- [ ] **Step 3: Implement**

`gridspine/static/loadflow.py`:
```python
"""Stage 2 (minimal): snapshot AC load flow. Dispatch arrives as the
validated table; results leave as plain frames keyed by canonical names."""
from dataclasses import dataclass, field

import pandapower as pp
import pandas as pd


@dataclass
class LFResult:
    converged: bool
    bus: pd.DataFrame = field(default_factory=pd.DataFrame)
    branch_loading: pd.DataFrame = field(default_factory=pd.DataFrame)
    slack_p_mw: float = float("nan")


def apply_dispatch(net, table, hour, registry) -> None:
    snap = table[table["hour"] == hour].set_index("unit_id")
    name_to_gen_idx = {net.gen.at[i, "name"]: i for i in net.gen.index}
    for unit_id, rec in registry.iterrows():
        if rec["kind"] != "gen":
            continue  # ext_grid is the slack; it absorbs the residual
        row = snap.loc[unit_id]
        i = name_to_gen_idx[unit_id]
        net.gen.at[i, "p_mw"] = float(row["p_mw"])
        net.gen.at[i, "in_service"] = bool(int(row["status"]))


def run_lf(net) -> LFResult:
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return LFResult(converged=False)
    bus = pd.DataFrame(
        {"vm_pu": net.res_bus["vm_pu"].values, "va_degree": net.res_bus["va_degree"].values},
        index=pd.Index(net.bus["name"].values, name="bus"),
    )
    line_loading = pd.DataFrame(
        {"loading_percent": net.res_line["loading_percent"].values},
        index=pd.Index([f"L_{i:02d}" for i in net.line.index], name="branch"),
    )
    trafo_loading = pd.DataFrame(
        {"loading_percent": net.res_trafo["loading_percent"].values},
        index=pd.Index([f"T_{i:02d}" for i in net.trafo.index], name="branch"),
    )
    return LFResult(
        converged=bool(net.converged),
        bus=bus,
        branch_loading=pd.concat([line_loading, trafo_loading]),
        slack_p_mw=float(net.res_ext_grid["p_mw"].sum()),
    )
```
Branch index `L_xx`/`T_xx` must match Task 5's PyPSA branch names — same format string; asserted end to end in Task 10.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add gridspine/static/loadflow.py tests/gridspine/test_loadflow.py
git commit --dry-run -- gridspine/static/loadflow.py tests/gridspine/test_loadflow.py
git commit -m "feat(gridspine): dispatch application and AC load flow stage" -- gridspine/static/loadflow.py tests/gridspine/test_loadflow.py
```

---

### Task 8: Handoff — PSS/E .raw v33 writer **[FABLE]**

**Files:**
- Create: `gridspine/handoff/raw_writer.py`
- Test: `tests/gridspine/test_raw_writer.py`

**Interfaces:**
- Consumes: pandapower net with canonical names + applied dispatch (post Task 7).
- Produces: `write_raw(net, path, title="gridspine export") -> dict[str, int]` — writes v33, returns `{bus_name: bus_number}` (numbers 1..N in `net.bus.index` order; deterministic — part of the contract). Canonical name in the bus NAME field. Sections: header, BUS, LOAD, FIXED SHUNT (empty), GENERATOR, BRANCH, TRANSFORMER (2-winding, 4-line v33 records), then empty-section terminators through Q. Impedances p.u. on 100 MVA system base / bus voltage base. Out-of-service gens written `STAT=0`, not omitted — the .dyr later needs the unit to exist.

- [ ] **Step 1: Probe pandapower's .raw importer availability (record output)**

Run: `pixi run python -c "import pandapower.converter as c; print(sorted(x for x in dir(c) if 'pss' in x.lower() or 'raw' in x.lower()))"`
If a `from_psse`-style function exists, the round-trip test uses it; if not, that test skips with the probe output as reason — the toy-net token tests and PowerFactory import (Task 9) remain the oracles. Do NOT write a bespoke parser just to round-trip.

- [ ] **Step 2: Write the failing tests**

`tests/gridspine/test_raw_writer.py`:
```python
import pandapower as pp
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39


def toy_net():
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=345.0, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=345.0, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.03, name="SLK_BUS_01")
    pp.create_gen(net, bus=b2, p_mw=120.0, vm_pu=1.02, sn_mva=200.0, name="G_BUS_02")
    pp.create_load(net, bus=b2, p_mw=100.0, q_mvar=30.0)
    pp.create_line_from_parameters(
        net, from_bus=b1, to_bus=b2, length_km=1.0,
        r_ohm_per_km=11.9025, x_ohm_per_km=119.025,  # 0.01 / 0.10 pu @ 100 MVA, 345 kV
        c_nf_per_km=0.0, max_i_ka=0.418,
    )
    return net


def _records(text, section_idx):
    """Split into sections on '0 /' terminators; return comma-token lists."""
    sections, cur = [], []
    for line in text.splitlines()[3:]:  # skip header + 2 title lines
        if line.strip().startswith("0 /") or line.strip() == "Q":
            sections.append(cur)
            cur = []
        else:
            cur.append([t.strip().strip("'").strip() for t in line.split(",")])
    return sections[section_idx]


def test_header_and_determinism(tmp_path):
    net = toy_net()
    m1 = write_raw(net, tmp_path / "a.raw")
    m2 = write_raw(net, tmp_path / "b.raw")
    assert m1 == m2 == {"BUS_01": 1, "BUS_02": 2}
    head = (tmp_path / "a.raw").read_text().splitlines()[0]
    assert head.split(",")[0].strip() == "0"          # IC
    assert float(head.split(",")[1]) == 100.0          # SBASE
    assert head.split(",")[2].strip() == "33"          # version


def test_bus_records(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "t.raw")
    buses = _records((tmp_path / "t.raw").read_text(), 0)
    assert len(buses) == 2
    assert (buses[0][0], buses[0][1], buses[0][3]) == ("1", "BUS_01", "3")  # slack IDE=3
    assert float(buses[0][2]) == pytest.approx(345.0)
    assert buses[1][3] == "2"                          # gen bus IDE=2


def test_branch_impedance_in_pu(tmp_path):
    net = toy_net()
    write_raw(net, tmp_path / "t.raw")
    branches = _records((tmp_path / "t.raw").read_text(), 4)
    assert len(branches) == 1
    assert float(branches[0][3]) == pytest.approx(0.01, rel=1e-3)   # R pu
    assert float(branches[0][4]) == pytest.approx(0.10, rel=1e-3)   # X pu


def test_offline_gen_written_with_stat0(tmp_path):
    net = toy_net()
    net.gen.loc[net.gen["name"] == "G_BUS_02", "in_service"] = False
    write_raw(net, tmp_path / "t.raw")
    gens = _records((tmp_path / "t.raw").read_text(), 3)
    grec = [g for g in gens if g[0] == "2"][0]
    assert grec[14] == "0"                             # STAT field


def test_case39_roundtrip_via_pandapower_importer(tmp_path):
    conv = pytest.importorskip("pandapower.converter")
    from_psse = getattr(conv, "from_psse", None)
    if from_psse is None:
        pytest.skip("pandapower.converter.from_psse unavailable (see Task 8 probe)")
    net = load_case39()
    write_raw(net, tmp_path / "c39.raw")
    net2 = from_psse(str(tmp_path / "c39.raw"))
    assert len(net2.bus) == 39
    pp.runpp(net2)
    assert net2.converged
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.handoff.raw_writer'`

- [ ] **Step 4: Implement**

`gridspine/handoff/raw_writer.py`:
```python
"""PSS/E RAW v33 writer — the handoff contract's network half.
Impedances on 100 MVA system base; bus numbers deterministic from bus
table order; canonical names in the NAME field (<=12 chars, enforced by
schema). Offline units written STAT=0, not omitted: the .dyr and the
dynamic study need the unit to exist."""
import numpy as np

SBASE_MVA = 100.0
F_HZ = 50.0

_EMPTY_TAIL = [
    "AREA", "TWO-TERMINAL DC", "VSC DC LINE", "IMPEDANCE CORRECTION",
    "MULTI-TERMINAL DC", "MULTI-SECTION LINE", "ZONE", "INTER-AREA TRANSFER",
    "OWNER", "FACTS DEVICE", "SWITCHED SHUNT", "GNE", "INDUCTION MACHINE",
]


def _bus_numbers(net):
    return {net.bus.at[i, "name"]: k + 1 for k, i in enumerate(net.bus.index)}


def write_raw(net, path, title="gridspine export"):
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    slack_buses = set(net.ext_grid["bus"])
    gen_buses = set(net.gen["bus"]) | slack_buses
    lines = [f" 0, {SBASE_MVA:.2f}, 33, 0, 0, {F_HZ:.2f}", str(title)[:60], "gridspine v33 export"]

    for i in net.bus.index:
        ide = 3 if i in slack_buses else (2 if i in gen_buses else 1)
        lines.append(
            f"{nums[name_of.at[i]]},'{name_of.at[i]:<12s}',{net.bus.at[i, 'vn_kv']:9.4f},"
            f"{ide}, 1, 1, 1,1.00000, 0.00000,1.10000,0.90000,1.10000,0.90000"
        )
    lines.append("0 / END OF BUS DATA, BEGIN LOAD DATA")

    for _, ld in net.load.iterrows():
        lines.append(
            f"{nums[name_of.at[ld['bus']]]},'1 ',1, 1, 1,{ld['p_mw']:10.3f},{ld['q_mvar']:10.3f},"
            f" 0.000, 0.000, 0.000, 0.000, 1,1,0"
        )
    lines.append("0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA")
    lines.append("0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA")

    def gen_record(bus_idx, p_mw, vm_pu, mbase, stat):
        return (
            f"{nums[name_of.at[bus_idx]]},'1 ',{p_mw:10.3f},   0.000,9999.000,-9999.000,"
            f"{vm_pu:7.5f}, 0,{mbase:9.3f}, 0.00000,0.15000, 0.00000, 0.00000,1.00000,"
            f"{stat},100.0,{mbase:9.3f},   0.000, 1,1.0000"
        )

    for _, g in net.gen.iterrows():
        sn = g.get("sn_mva", np.nan)
        mbase = float(sn) if np.isfinite(sn) else SBASE_MVA
        lines.append(gen_record(g["bus"], g["p_mw"], g.get("vm_pu", 1.0), mbase,
                                1 if g["in_service"] else 0))
    for _, e in net.ext_grid.iterrows():
        lines.append(gen_record(e["bus"], 0.0, e.get("vm_pu", 1.0), SBASE_MVA, 1))
    lines.append("0 / END OF GENERATOR DATA, BEGIN BRANCH DATA")

    for _, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        z_base = vn ** 2 / SBASE_MVA
        r_pu = ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        x_pu = ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        b_pu = (2 * np.pi * F_HZ * ln["c_nf_per_km"] * 1e-9 * ln["length_km"]
                * ln["parallel"]) * z_base
        rate = np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"]
        lines.append(
            f"{nums[name_of.at[ln['from_bus']]]},{nums[name_of.at[ln['to_bus']]]},'1 ',"
            f"{r_pu:10.5f},{x_pu:10.5f},{b_pu:10.5f},{rate:8.2f},{rate:8.2f},{rate:8.2f},"
            f" 0.000, 0.000, 0.000, 0.000,{1 if ln['in_service'] else 0},1, 0.00, 1,1.0000"
        )
    lines.append("0 / END OF BRANCH DATA, BEGIN TRANSFORMER DATA")

    for _, tr in net.trafo.iterrows():
        sn = tr["sn_mva"]
        r_pu = (tr["vkr_percent"] / 100.0) * (SBASE_MVA / sn)
        z_pu = (tr["vk_percent"] / 100.0) * (SBASE_MVA / sn)
        x_pu = np.sqrt(max(z_pu ** 2 - r_pu ** 2, 1e-12))
        hv, lv = nums[name_of.at[tr["hv_bus"]]], nums[name_of.at[tr["lv_bus"]]]
        stat = 1 if tr["in_service"] else 0
        lines.append(
            f"{hv},{lv},0,'1 ',1,1,1, 0.00000, 0.00000,2,'TR_{hv:<9d}',{stat},1,1.0000"
        )
        lines.append(f"{r_pu:10.5f},{x_pu:10.5f},{SBASE_MVA:8.2f}")
        lines.append(
            f"1.00000, 0.000, 0.000,{sn:8.2f},{sn:8.2f},{sn:8.2f},0, 0,"
            f"1.10000,0.90000,1.10000,0.90000,33,0, 0.00000, 0.00000"
        )
        lines.append("1.00000, 0.000")
    lines.append("0 / END OF TRANSFORMER DATA, BEGIN AREA DATA")

    for k, name in enumerate(_EMPTY_TAIL[1:], start=1):
        lines.append(f"0 / END OF {_EMPTY_TAIL[k - 1]} DATA, BEGIN {name} DATA")
    lines.append(f"0 / END OF {_EMPTY_TAIL[-1]} DATA")
    lines.append("Q")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return nums
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass (round-trip may SKIP per the Step-1 probe — a skip is acceptable ONLY with the probe output quoted in the report). The 4-line transformer record is the most likely defect site — if the importer chokes, fix the writer, never relax the test.

- [ ] **Step 6: Commit**

```bash
git add gridspine/handoff/raw_writer.py tests/gridspine/test_raw_writer.py
git commit --dry-run -- gridspine/handoff/raw_writer.py tests/gridspine/test_raw_writer.py
git commit -m "feat(gridspine): PSS/E RAW v33 writer" -- gridspine/handoff/raw_writer.py tests/gridspine/test_raw_writer.py
```

---

### Task 9: PowerFactory comparison harness + export runbook **[Opus]**

**Files:**
- Create: `gridspine/readback/pf_compare.py`
- Create: `tests/gridspine/fixtures/powerfactory/README.md` (the runbook)
- Test: `tests/gridspine/test_pf_compare.py`

**Interfaces:**
- Consumes: `LFResult` (Task 7 — coordinate the dataclass fields from THIS PLAN, not the diff, if built in parallel).
- Produces: `compare_lf(lf: LFResult, pf_csv: Path, vm_tol=0.01, va_tol_deg=0.5) -> pd.DataFrame` — per-bus frame: `vm_pu_pp`, `vm_pu_pf`, `vm_rel_err`, `va_degree_pp`, `va_degree_pf`, `va_abs_err_deg`, `ok`. Raises `ContractError` on bus-set mismatch. `readback/` imports NO engine — pandas only.
- PF CSV contract (runbook): columns `bus_name,vm_pu,va_degree`, one row per bus, canonical names.

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_pf_compare.py`:
```python
import pandas as pd
import pytest

from gridspine.readback.pf_compare import compare_lf
from gridspine.schema.contracts import ContractError
from gridspine.static.loadflow import LFResult


def lf_result():
    bus = pd.DataFrame(
        {"vm_pu": [1.030, 0.985], "va_degree": [0.0, -5.2]},
        index=pd.Index(["BUS_01", "BUS_02"], name="bus"),
    )
    return LFResult(converged=True, bus=bus)


def test_within_tolerance_passes(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.0305,0.01\nBUS_02,0.9846,-5.15\n")
    cmp = compare_lf(lf_result(), csv)
    assert cmp["ok"].all()
    assert cmp.loc["BUS_01", "vm_rel_err"] == pytest.approx(0.0005 / 1.0305, rel=1e-3)


def test_out_of_tolerance_flagged_per_element(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.030,0.0\nBUS_02,1.100,-5.2\n")
    cmp = compare_lf(lf_result(), csv)
    assert bool(cmp.loc["BUS_01", "ok"]) and not bool(cmp.loc["BUS_02", "ok"])


def test_bus_set_mismatch_rejected(tmp_path):
    csv = tmp_path / "pf.csv"
    csv.write_text("bus_name,vm_pu,va_degree\nBUS_01,1.03,0.0\n")
    with pytest.raises(ContractError, match="bus set"):
        compare_lf(lf_result(), csv)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.readback.pf_compare'`

- [ ] **Step 3: Implement**

`gridspine/readback/pf_compare.py`:
```python
"""PowerFactory result read-back and <1% comparison — the phase-1 oracle.
Manual flow: import the .raw in PowerFactory, run Newton-Raphson LF,
export bus results per the fixture README, drop the CSV, run the test."""
import pandas as pd

from gridspine.schema.contracts import ContractError


def compare_lf(lf, pf_csv, vm_tol=0.01, va_tol_deg=0.5) -> pd.DataFrame:
    pf = pd.read_csv(pf_csv).set_index("bus_name")
    if set(pf.index) != set(lf.bus.index):
        raise ContractError(
            f"bus set mismatch: only-pandapower={sorted(set(lf.bus.index) - set(pf.index))} "
            f"only-powerfactory={sorted(set(pf.index) - set(lf.bus.index))}"
        )
    out = pd.DataFrame(index=lf.bus.index.copy())
    out["vm_pu_pp"] = lf.bus["vm_pu"]
    out["vm_pu_pf"] = pf["vm_pu"]
    out["vm_rel_err"] = (out["vm_pu_pp"] - out["vm_pu_pf"]).abs() / out["vm_pu_pf"].abs()
    out["va_degree_pp"] = lf.bus["va_degree"]
    out["va_degree_pf"] = pf["va_degree"]
    out["va_abs_err_deg"] = (out["va_degree_pp"] - out["va_degree_pf"]).abs()
    out["ok"] = (out["vm_rel_err"] < vm_tol) & (out["va_abs_err_deg"] < va_tol_deg)
    return out
```

- [ ] **Step 4: Write the runbook**

`tests/gridspine/fixtures/powerfactory/README.md`:
```markdown
# PowerFactory validation fixtures (manual export — Hao)

1. In PowerFactory: File > Import > PSS/E raw, pick the `case39_dispatch.raw`
   produced by the Task 10 CLI (artifact dir printed on run).
2. Run a Newton-Raphson AC load flow (balanced, positive sequence),
   default convergence settings.
3. Export per-bus results to CSV with EXACTLY these columns:
   `bus_name,vm_pu,va_degree`
   - `bus_name` = the canonical name from the .raw NAME field (BUS_01…).
   - `vm_pu` = voltage magnitude p.u.; `va_degree` = angle in degrees.
4. Save as `case39_h<hour>.csv` in THIS directory (e.g. `case39_h19.csv`).
5. Run `pixi run gridspine-tests` — the vertical-slice test picks the
   fixture up automatically; it SKIPS while no fixture exists.

Gate: |Vm| within 1% relative AND angle within 0.5 deg absolute, per bus.
A single failing bus fails the slice.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add gridspine/readback/pf_compare.py tests/gridspine/test_pf_compare.py tests/gridspine/fixtures/powerfactory/README.md
git commit --dry-run -- gridspine/readback/pf_compare.py tests/gridspine/test_pf_compare.py tests/gridspine/fixtures/powerfactory/README.md
git commit -m "feat(gridspine): PowerFactory comparison harness and export runbook" -- gridspine/readback/pf_compare.py tests/gridspine/test_pf_compare.py tests/gridspine/fixtures/powerfactory/README.md
```

---

### Task 10: Driver — 39-bus vertical slice end to end **[Opus, Fable review]**

**Files:**
- Create: `gridspine/drivers/planning.py`
- Create: `gridspine/schema/errors.py`
- Test: `tests/gridspine/test_vertical_slice.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `run_39bus_slice(outdir: Path, hour: int = 19) -> SliceResult` — dataclass `SliceResult(converged: bool, artifacts: dict[str, Path], lf: LFResult)`. Chain: `load_case39` → `to_pypsa` → `run_uc` → `to_dispatch_table` → `apply_dispatch(hour)` → `run_lf` → `write_raw`. Artifacts in `outdir`: `dispatch.csv`, `lf_bus.csv`, `lf_branch.csv`, `case39_dispatch.raw`, `manifest.json` (stages, hour, ledger).
  - `gridspine/schema/errors.py`: `StageError(stage, element_ids, cause)` dataclass with `write(outdir) -> Path` dumping `error_<stage>.json`. Driver writes it on failure and re-raises — resume machinery later; the artifact contract lands now.
  - CLI: `pixi run python -m gridspine.drivers.planning --out <dir> [--hour 19]`.

- [ ] **Step 1: Write the failing tests**

`tests/gridspine/test_vertical_slice.py`:
```python
import json
from pathlib import Path

import pytest

from gridspine.drivers.planning import run_39bus_slice
from gridspine.readback.pf_compare import compare_lf

FIXDIR = Path(__file__).parent / "fixtures" / "powerfactory"


@pytest.fixture(scope="module")
def slice_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("slice")
    return run_39bus_slice(out, hour=19), out


def test_slice_runs_and_converges(slice_result):
    res, out = slice_result
    assert res.converged
    for key in ("dispatch", "lf_bus", "lf_branch", "raw", "manifest"):
        assert res.artifacts[key].exists(), key


def test_manifest_records_assumptions(slice_result):
    res, _ = slice_result
    manifest = json.loads(res.artifacts["manifest"].read_text())
    assert manifest["hour"] == 19
    ledger_text = " ".join(manifest["ledger"])
    assert "q_mvar" in ledger_text and "LOAD_SHAPE" in ledger_text


def test_raw_and_lf_use_same_bus_names(slice_result):
    res, _ = slice_result
    raw_text = res.artifacts["raw"].read_text()
    for bus in res.lf.bus.index:
        assert bus in raw_text


def test_powerfactory_gate(tmp_path):
    fixture = FIXDIR / "case39_h19.csv"
    if not fixture.exists():
        pytest.skip("PowerFactory fixture not yet exported (see fixtures/powerfactory/README.md)")
    res = run_39bus_slice(tmp_path, hour=19)
    cmp = compare_lf(res.lf, fixture)
    failing = cmp[~cmp["ok"]]
    assert failing.empty, f"buses outside 1%/0.5deg:\n{failing}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run gridspine-tests`
Expected: FAIL — `ModuleNotFoundError: No module named 'gridspine.drivers.planning'`

- [ ] **Step 3: Implement**

`gridspine/schema/errors.py`:
```python
"""Typed stage-failure artifact — UI and chatbot render this same file."""
import dataclasses
import json
from pathlib import Path


@dataclasses.dataclass
class StageError:
    stage: str
    element_ids: list
    cause: str

    def write(self, outdir) -> Path:
        p = Path(outdir) / f"error_{self.stage}.json"
        p.write_text(json.dumps(dataclasses.asdict(self), indent=2))
        return p
```

`gridspine/drivers/planning.py`:
```python
"""Variant-1 driver, increment-1 scope: the 39-bus vertical slice.
Each stage writes its artifact before the next starts — the file IS the
boundary. Engines only via the caged modules."""
import argparse
import dataclasses
import json
from pathlib import Path

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.producers.pypsa_nodal import run_uc, to_dispatch_table, to_pypsa
from gridspine.schema.errors import StageError
from gridspine.static.loadflow import LFResult, apply_dispatch, run_lf

LEDGER = [
    "q_mvar=0 in dispatch table: gens are PV nodes, Q is an LF result (assumed)",
    "LOAD_SHAPE is a synthetic 24 h profile, peak hour 19, valley hour 3 (assumed)",
    "ext_grid modelled as 3000 MW import at 80 EUR/MWh marginal cost (assumed)",
]


@dataclasses.dataclass
class SliceResult:
    converged: bool
    artifacts: dict
    lf: LFResult


def run_39bus_slice(outdir, hour: int = 19) -> SliceResult:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    art = {}
    stage = "ingest"
    try:
        net = load_case39()
        registry = registry_from_net(net)

        stage = "dispatch"
        n = to_pypsa(net)
        table = to_dispatch_table(run_uc(n))
        art["dispatch"] = outdir / "dispatch.csv"
        table.to_csv(art["dispatch"], index=False)

        stage = "loadflow"
        apply_dispatch(net, table, hour=hour, registry=registry)
        lf = run_lf(net)
        art["lf_bus"] = outdir / "lf_bus.csv"
        art["lf_branch"] = outdir / "lf_branch.csv"
        lf.bus.to_csv(art["lf_bus"])
        lf.branch_loading.to_csv(art["lf_branch"])

        stage = "handoff"
        art["raw"] = outdir / "case39_dispatch.raw"
        write_raw(net, art["raw"], title=f"case39 UC dispatch hour {hour}")

        art["manifest"] = outdir / "manifest.json"
        art["manifest"].write_text(json.dumps({
            "stages": ["ingest", "dispatch", "loadflow", "handoff"],
            "network": "pandapower case39, canonical names",
            "hour": hour,
            "ledger": LEDGER,
        }, indent=2))
        return SliceResult(converged=lf.converged, artifacts=art, lf=lf)
    except Exception as exc:
        StageError(stage=stage, element_ids=[], cause=repr(exc)).write(outdir)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hour", type=int, default=19)
    args = ap.parse_args()
    res = run_39bus_slice(args.out, hour=args.hour)
    print(f"converged={res.converged}")
    for k, p in res.artifacts.items():
        print(f"{k}: {p}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run gridspine-tests`
Expected: all pass except `test_powerfactory_gate` SKIPPED (fixture pending manual export). That is the ONLY acceptable skip in the suite.

- [ ] **Step 5: Run the CLI once, keep the artifact dir for Hao**

Run: `pixi run python -m gridspine.drivers.planning --out results/gridspine_slice`
Expected: `converged=True` + artifact paths. Hand Hao: import `results/gridspine_slice/case39_dispatch.raw` in PowerFactory per the fixtures README, drop the CSV, re-run to arm the <1% gate.

- [ ] **Step 6: Full-suite regression + commit**

Run: `pixi run gridspine-tests` (green, 1 documented skip)
```bash
git add gridspine/drivers/planning.py gridspine/schema/errors.py tests/gridspine/test_vertical_slice.py
git commit --dry-run -- gridspine/drivers/planning.py gridspine/schema/errors.py tests/gridspine/test_vertical_slice.py
git commit -m "feat(gridspine): 39-bus vertical-slice driver and CLI" -- gridspine/drivers/planning.py gridspine/schema/errors.py tests/gridspine/test_vertical_slice.py
```

---

## Parallelism map (for the dispatching master)

- Tasks 2, 3 are independent after Task 1 → dispatch in ONE message, two Agent calls.
- Task 4 needs 3; Task 5 needs 4; Task 6 needs 5+2; Task 7 needs 4+2; Task 8 needs 4; Task 9 needs only 7's `LFResult` SHAPE (from this plan).
- After Task 4 lands: lanes {5→6}, {7}, {8}, {9} are independent → dispatch 5, 7, 8, 9 concurrently in one message.
- Task 10 is the integration barrier: needs everything.

## After increment 1

Manual PowerFactory export (Hao) arms `test_powerfactory_gate`. Green at <1% = increment 1 DONE. Next per spec phasing: snapshot ranking over 8760 h (weeks 5–8). Any <1% failure is a Fable-level analysis task — interpret the mismatch (trafo model, shunt handling, slack distribution) before touching code.
