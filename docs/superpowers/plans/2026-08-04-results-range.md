# Results Range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a caller ask for a positional slice of any `/results/*` time series, so the canvas overlay stops downloading ~470 MB to read one row.

**Architecture:** One pure `slice_ts` helper in `services/serialization.py` does the slicing and reports what it served. `_serve_ts` threads two query parameters through for the eleven endpoints it already owns; five bespoke endpoints call `slice_ts` directly. The frontend picks a chunk size from the asset count, aligns requests to chunk boundaries so React Query caches them, and offsets its row lookup by the served range.

**Tech Stack:** Python 3.12 / FastAPI / pandas, pytest; React 18 / TypeScript / axios / TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-08-04-results-range-design.md`

## Global Constraints

- **Branch is `feature/local-app-impl`.** Re-run `git branch --show-current` before every commit. Other sessions share this worktree — leave the tree clean when pausing.
- **Never write to or delete under `pypsa-gui/backend/projects/`, `~/Documents/PyPSA GUI/`, or `~/Documents/PyPSA Studio/`.**
- **Never write an API key value into a report, commit message or log.** `backend/.env` holds a live one; describe shape, never characters.
- **No downsampling, ever.** 85 client call sites compute energy and cost totals by summing every row via `shared.tsx::weightedSum`. Averaging would make every MWh and € figure depend on the zoom level. Slicing is safe *because* it returns the exact rows requested.
- **Bounds are INCLUSIVE and POSITIONAL** — indices into the snapshot axis, never timestamps.
- **`complete` is computed from what was SERVED, not what was ASKED:** `from == 0 and to == total - 1` after clamping.
- **A request with no `from`/`to` must be byte-identical to today's response** — no `range` key, no other change. This is what keeps every untouched consumer working.
- **Backend test command:** `cd "<repo-root>" && pixi run gui-tests <pytest args>`. Never pipe into `tail`/`head` (a pipeline reports only its last stage, hiding failures behind exit 0). Never pass `-q` (pytest.ini sets it; a second becomes `-qq`).
- **Frontend commands need the pixi bin on PATH:**
  ```bash
  cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
  export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
  ```
  A bare `npx`/`node` giving `command not found` is a PATH problem, not a missing dependency. Never `npm install`.
- **Use path-limited `git commit <paths>`, never `git add -A`.** New files need `git add <path>` first.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `pypsa-gui/backend/services/serialization.py` | modify | `slice_ts`; `ts_payload` gains `range_meta` |
| `pypsa-gui/backend/tests/test_serialization_slice.py` | create | `slice_ts` unit tests |
| `pypsa-gui/backend/routers/results.py` | modify | `_serve_ts` + 16 endpoint signatures |
| `pypsa-gui/backend/tests/test_results_range.py` | create | contract loop + equivalence invariant |
| `pypsa-gui/frontend/src/pages/results/chunking.ts` | create | `chooseChunk`, `chunkBounds` |
| `pypsa-gui/frontend/src/pages/results/chunking.test.ts` | create | chunking unit tests |
| `pypsa-gui/frontend/src/api/simulation.ts` | modify | `tsParams`; range args on 9 getters |
| `pypsa-gui/frontend/src/components/CanvasResultsContext.tsx` | modify | chunked queries + offset row lookup |

---

### Task 1: `slice_ts` and the `range` block

**Files:**
- Modify: `pypsa-gui/backend/services/serialization.py`
- Create: `pypsa-gui/backend/tests/test_serialization_slice.py`

**Interfaces:**
- Consumes: `safe_values(df)` (existing, `serialization.py:67`).
- Produces, for Tasks 2 and 3:
  - `MAX_RESPONSE_VALUES: int`
  - `slice_ts(df, from_, to_, *, max_values=MAX_RESPONSE_VALUES) -> tuple[pd.DataFrame, dict]`
  - `ts_payload(df, *, extra=None, range_meta=None) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/backend/tests/test_serialization_slice.py`:

```python
"""
Positional slicing for /results/* time series.

Pure-function tests: no app, no network, no fixtures. `slice_ts` imports
nothing from FastAPI on purpose, which is what makes this possible.
"""
import numpy as np
import pandas as pd
import pytest

from services.serialization import MAX_RESPONSE_VALUES, slice_ts, ts_payload


def frame(rows: int, cols: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        np.arange(rows * cols, dtype=float).reshape(rows, cols),
        index=pd.date_range("2030-01-01", periods=rows, freq="h"),
        columns=[f"A{i}" for i in range(cols)],
    )


def multi_frame(periods=(2030, 2035), per_period=4, cols=2) -> pd.DataFrame:
    idx = pd.MultiIndex.from_product(
        [list(periods), pd.date_range("2030-01-01", periods=per_period, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    return pd.DataFrame(
        np.arange(len(idx) * cols, dtype=float).reshape(len(idx), cols),
        index=idx, columns=[f"A{i}" for i in range(cols)],
    )


def test_no_bounds_returns_everything_and_reports_complete():
    df = frame(10)

    out, meta = slice_ts(df, None, None)

    assert len(out) == 10
    assert meta == {"from": 0, "to": 9, "total": 10, "complete": True, "capped": False}


def test_bounds_are_inclusive():
    """from=2, to=4 must return rows 2, 3 AND 4 — three rows, not two."""
    out, meta = slice_ts(frame(10), 2, 4)

    assert len(out) == 3
    assert out.iloc[0]["A0"] == 6.0    # row 2, first column of arange(30).reshape(10,3)
    assert out.iloc[-1]["A0"] == 12.0  # row 4
    assert (meta["from"], meta["to"]) == (2, 4)


def test_negative_from_clamps_to_zero():
    _out, meta = slice_ts(frame(10), -5, 3)

    assert meta["from"] == 0


def test_to_beyond_the_end_clamps_and_still_reports_complete():
    """
    Asking for more rows than exist and receiving all of them IS complete.
    Reporting False would make a consumer refuse to total data it holds whole.
    """
    out, meta = slice_ts(frame(10), 0, 99999)

    assert len(out) == 10
    assert meta["to"] == 9
    assert meta["complete"] is True


def test_a_window_is_not_complete():
    _out, meta = slice_ts(frame(10), 1, 8)

    assert meta["complete"] is False


def test_inverted_range_yields_empty_rather_than_raising():
    """The client already treats from>to as empty; a 400 would toast an error."""
    out, meta = slice_ts(frame(10), 7, 3)

    assert len(out) == 0
    assert meta["total"] == 10
    assert meta["complete"] is False


def test_multiindex_slices_positionally_and_keeps_period_alignment():
    df = multi_frame()          # 2 periods x 4 timesteps = 8 rows

    out, meta = slice_ts(df, 4, 7)

    assert len(out) == 4
    assert list(out.index.get_level_values(0)) == [2035, 2035, 2035, 2035]
    assert meta["total"] == 8


def test_row_cap_trips_and_is_reported():
    df = frame(1000, cols=10)

    out, meta = slice_ts(df, 0, 999, max_values=100)

    assert len(out) == 10          # 100 values / 10 columns
    assert meta["capped"] is True
    assert meta["complete"] is False


def test_row_cap_does_not_trip_below_the_limit():
    _out, meta = slice_ts(frame(10, cols=2), None, None, max_values=MAX_RESPONSE_VALUES)

    assert meta["capped"] is False


def test_empty_frame_does_not_raise():
    out, meta = slice_ts(frame(0), 0, 5)

    assert len(out) == 0
    assert meta["total"] == 0


def test_ts_payload_without_range_meta_is_unchanged():
    """The no-range path must stay byte-identical for existing consumers."""
    payload = ts_payload(frame(3))

    assert "range" not in payload
    assert set(payload) == {"index", "columns", "data"}


def test_ts_payload_emits_the_range_block():
    df, meta = slice_ts(frame(10), 2, 4)

    payload = ts_payload(df, range_meta=meta)

    assert payload["range"] == {
        "from": 2, "to": 4, "total": 10, "complete": False, "capped": False,
    }
    assert len(payload["data"]) == 3


def test_range_meta_wins_over_a_colliding_extra():
    """`range` is authoritative — an endpoint's extra dict must not shadow it."""
    df, meta = slice_ts(frame(10), 0, 1)

    payload = ts_payload(df, extra={"range": "nonsense"}, range_meta=meta)

    assert payload["range"] == meta
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_serialization_slice.py -v > /tmp/r1.log 2>&1; echo "EXIT=$?"
```

Expected: collection error — `ImportError: cannot import name 'MAX_RESPONSE_VALUES' from 'services.serialization'`.

- [ ] **Step 3: Implement `slice_ts`**

In `pypsa-gui/backend/services/serialization.py`, after `safe_values` (ends at `:72`):

```python
# ~10 bytes per JSON float was measured on a frame of the exact shape
# `ts_payload` emits: 5,256,000 values serialised to 52.6 MB. So this cap is
# roughly a 20 MB response — large enough that no legitimate UI request hits
# it, small enough that a hand-crafted one cannot ask the server to build
# something nobody can use.
MAX_RESPONSE_VALUES = 2_000_000


def slice_ts(
    df: pd.DataFrame,
    from_: int | None,
    to_: int | None,
    *,
    max_values: int = MAX_RESPONSE_VALUES,
) -> tuple[pd.DataFrame, dict]:
    """
    Positionally slice a time-series frame. Returns ``(sliced, range_meta)``.

    Bounds are INCLUSIVE and POSITIONAL — indices into the snapshot axis, not
    timestamps. Positional addressing is what lets one implementation serve
    both shapes: ``df.iloc[a:b]`` does not care whether the index is a flat
    DatetimeIndex or a ``(period, timestep)`` MultiIndex. A timestamp would be
    ambiguous on multi-period networks, which replicate ONE operational year
    under every investment period — the same trap CLAUDE.md documents against
    the Horizon filter.

    Clamping mirrors the frontend's own ``Math.max(0, Math.min(...))`` in
    ``shared.tsx::weightedSum``, so client and server agree by construction
    rather than by coincidence.

    ``from_ > to_`` yields an EMPTY frame rather than raising. The client
    already treats an inverted range as empty (``if (rFrom > rTo) return 0``),
    and a 400 would turn a benign transient UI state into an error toast. The
    returned meta states exactly what was served, so nothing is silent.

    ``complete`` is computed from what was SERVED, not what was ASKED: a
    request for row 99,999 of a 168-row series returns ``complete: True``,
    because the caller does hold every row. Reporting False there would make a
    consumer refuse to total data that is in fact whole.
    """
    total = len(df.index)
    if total == 0:
        return df, {"from": 0, "to": -1, "total": 0, "complete": True, "capped": False}

    lo = 0 if from_ is None else max(0, int(from_))
    hi = total - 1 if to_ is None else min(total - 1, int(to_))

    if lo > hi:
        return df.iloc[0:0], {
            "from": lo, "to": hi, "total": total, "complete": False, "capped": False,
        }

    capped = False
    width = max(1, len(df.columns))
    if (hi - lo + 1) * width > max_values:
        hi = min(lo + max(1, max_values // width) - 1, total - 1)
        capped = True

    return df.iloc[lo : hi + 1], {
        "from": lo,
        "to": hi,
        "total": total,
        "complete": lo == 0 and hi == total - 1,
        "capped": capped,
    }
```

- [ ] **Step 4: Add `range_meta` to `ts_payload`**

Change the signature at `serialization.py:107` and the tail of the function:

```python
def ts_payload(
    df: pd.DataFrame,
    *,
    extra: dict | None = None,
    range_meta: dict | None = None,
) -> dict:
```

and replace the final three lines (`if extra: payload.update(extra)` / `return payload`) with:

```python
    if extra:
        payload.update(extra)
    # AFTER `extra`, deliberately: `range` describes what was served and must
    # not be shadowed by an endpoint's own extra dict.
    if range_meta is not None:
        payload["range"] = range_meta
    return payload
```

Leave the docstring's existing multi-period paragraph intact and add one line noting that `range` appears only when `range_meta` is supplied.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_serialization_slice.py -v > /tmp/r1.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`, 13 passed.

- [ ] **Step 6: Confirm no existing consumer moved**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_results_endpoints.py tests/test_multi_period_results.py -v > /tmp/r1b.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`. If either file does not exist, run `pixi run gui-tests -k "results" -v` instead and report which files ran. The point is to prove the unranged path is unchanged.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current   # must print feature/local-app-impl
git add pypsa-gui/backend/tests/test_serialization_slice.py
git commit pypsa-gui/backend/services/serialization.py pypsa-gui/backend/tests/test_serialization_slice.py -m "feat(gui): positional slicing for time-series payloads

slice_ts returns the rows asked for plus a range block saying what was
served. Inclusive positional bounds, because df.iloc behaves identically on
a flat DatetimeIndex and a (period, timestep) MultiIndex while a timestamp
matches N rows on a multi-period network.

complete is derived from what was served, not what was asked: holding every
row after a clamp IS complete, and saying otherwise would make a consumer
refuse to total whole data."
```

---

### Task 2: Range on the eleven `_serve_ts` endpoints

**Files:**
- Modify: `pypsa-gui/backend/routers/results.py`
- Create: `pypsa-gui/backend/tests/test_results_range.py`

**Interfaces:**
- Consumes: `slice_ts`, `ts_payload(..., range_meta=)` from Task 1.
- Produces, for Task 3: the `_serve_ts` signature below, and the test file that Task 3 extends to all sixteen endpoints.

**The eleven** (verified by parsing the router, not by grep): `/generators`, `/storage_dispatch`, `/store_dispatch`, `/store_energy`, `/storage`, `/lines`, `/links`, `/transformers`, `/voltages`, `/line_reactive`, `/transformer_reactive`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_results_range.py`:

```python
"""
The `from`/`to` contract on the /results/* series endpoints.

Solves a small network ONCE per module so the dispatch-freshness gate passes;
conftest's autouse `reset_backend` means the network must be re-installed per
test, which `solved_client` does.
"""
import pytest

# Endpoints served by `_serve_ts`. Task 3 extends this to all sixteen.
SERVE_TS_ENDPOINTS = [
    "/api/results/generators",
    "/api/results/storage_dispatch",
    "/api/results/store_dispatch",
    "/api/results/store_energy",
    "/api/results/storage",
    "/api/results/lines",
    "/api/results/links",
    "/api/results/transformers",
    "/api/results/voltages",
    "/api/results/line_reactive",
    "/api/results/transformer_reactive",
]


def _ranged(client, url, **params):
    r = client.get(url, params=params)
    assert r.status_code in (200, 204), f"{url} -> {r.status_code}: {r.text[:400]}"
    return r


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_endpoint_accepts_from_and_to(solved_client, url):
    """A 422 here means the endpoint never declared the parameters."""
    r = _ranged(solved_client, url, **{"from": 0, "to": 0})

    assert r.status_code != 422


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_ranged_response_echoes_what_it_served(solved_client, url):
    r = _ranged(solved_client, url, **{"from": 0, "to": 0})
    if r.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")

    body = r.json()
    assert "range" in body, f"{url} returned no range block"
    assert set(body["range"]) == {"from", "to", "total", "complete", "capped"}
    assert body["range"]["from"] == 0
    assert body["range"]["to"] == 0
    assert len(body["data"]) == 1, "one row requested, one row expected"


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_unranged_response_carries_no_range_key(solved_client, url):
    """The no-parameter path must stay byte-identical for existing consumers."""
    r = _ranged(solved_client, url)
    if r.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")

    assert "range" not in r.json()


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_a_server_slice_equals_the_same_slice_taken_client_side(solved_client, url):
    """
    The invariant this whole feature rests on: moving the window from after
    the download to before it must not change a single value. This is also
    what catches an off-by-one in the inclusive bounds.
    """
    full = _ranged(solved_client, url)
    if full.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")
    full_body = full.json()
    if len(full_body["data"]) < 3:
        pytest.skip(f"{url} has too few rows to window")

    sliced = _ranged(solved_client, url, **{"from": 1, "to": 2}).json()

    assert sliced["columns"] == full_body["columns"]
    assert sliced["index"] == full_body["index"][1:3]
    assert sliced["data"] == full_body["data"][1:3]


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_complete_is_false_for_a_window_and_true_for_the_whole(solved_client, url):
    full = _ranged(solved_client, url)
    if full.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")
    total = len(full.json()["data"])
    if total < 2:
        pytest.skip(f"{url} has too few rows to window")

    whole = _ranged(solved_client, url, **{"from": 0, "to": total - 1}).json()
    window = _ranged(solved_client, url, **{"from": 0, "to": 0}).json()

    assert whole["range"]["complete"] is True
    assert window["range"]["complete"] is False


def test_a_one_row_request_is_small(solved_client):
    """The canvas win, as a regression guard rather than a claim."""
    r = _ranged(solved_client, "/api/results/generators", **{"from": 0, "to": 0})
    if r.status_code == 204:
        pytest.skip("no generator dispatch on this fixture network")

    assert len(r.content) < 8_192, f"one row serialised to {len(r.content)} bytes"
```

Add the `solved_client` fixture at the top of the same file:

```python
@pytest.fixture
def solved_client(client, install_network):
    """
    A client whose backend holds a solved network, so `_dispatch_ready` passes.

    Reuses the golden fixture built for the trustworthy-numbers work: it is
    already solved by a real HiGHS run and has generators, storage, lines and
    links, which is the breadth this contract loop needs.
    """
    from tests.golden.fixture import install_golden

    install_golden()
    return client
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_results_range.py -v > /tmp/r2.log 2>&1; echo "EXIT=$?"
```

Expected: failures on `test_ranged_response_echoes_what_it_served` — the endpoints ignore unknown query parameters today, so they return a full payload with no `range` key. Report the actual counts. If instead you see a fixture error, resolve that first and report what `install_golden` required.

- [ ] **Step 3: Import `Query` and thread the parameters through `_serve_ts`**

In `pypsa-gui/backend/routers/results.py`, change the FastAPI import at `:22`:

```python
from fastapi import APIRouter, Query, Response
```

Add `slice_ts` to the `services.serialization` import block at `:27`:

```python
from services.serialization import (
    df_to_json,
    safe_float as _safe_float,
    safe_values as _safe_values,
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
)
```

Replace the body of `_serve_ts` (`:129-155`), keeping its docstring and adding a paragraph about the new parameters:

```python
def _serve_ts(
    accessor: str,
    attr: str,
    source: str,
    *,
    from_: int | None = None,
    to_: int | None = None,
    echo_source: bool = False,
):
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        df = _result_df(n, accessor, attr, source)
        if df is None or df.empty:
            return _not_solved()
        # No bounds supplied → `range_meta` stays None and the payload is
        # byte-identical to the pre-range response. That is what keeps every
        # consumer that has not been converted working unchanged.
        if from_ is None and to_ is None:
            range_meta = None
        else:
            df, range_meta = _slice_ts(df, from_, to_)
        extra = {"source": source} if echo_source else None
        return _ts_payload(df, extra=extra, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()
```

- [ ] **Step 4: Declare the parameters on all eleven endpoints**

Each of the eleven takes the same two additions. `alias="from"` is required because `from` is a Python keyword. Full example for `/generators` (`:835`):

```python
@results_router.get("/generators")
def get_generator_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    return _serve_ts("generators_t", "p", source, from_=from_, to_=to_)
```

Apply the identical pattern to the other ten, preserving each existing docstring and each existing `source` default (`"lopf"` for `/storage_dispatch`, `/store_dispatch`, `/store_energy`, `/storage`, `/lines`, `/links`, `/transformers`; **`"ac_pf"`** for `/voltages`, `/line_reactive`, `/transformer_reactive`). Do not change any default.

`/voltages`, `/line_reactive` and `/transformer_reactive` pass `echo_source=True` today — keep that.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_results_range.py -v > /tmp/r2.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`. Skips are acceptable where the fixture network has no data for an endpoint (`/transformers`, the reactive ones); report which skipped and why.

- [ ] **Step 6: Prove the tests discriminate**

Temporarily delete `from_=from_, to_=to_` from `/generators`' call to `_serve_ts`, re-run, and confirm `test_ranged_response_echoes_what_it_served[/api/results/generators]` FAILS. Restore, confirm green, and confirm `git diff` on the router is empty. Report both observations with counts.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/backend/tests/test_results_range.py
git commit pypsa-gui/backend/routers/results.py pypsa-gui/backend/tests/test_results_range.py -m "feat(gui): snapshot range on the eleven _serve_ts result endpoints

One helper change plus eleven signatures. With no bounds supplied the
payload is byte-identical to before, so unconverted consumers are untouched.

The contract loop asserts the invariant the feature rests on: a server-side
slice equals the same slice taken client-side from the full payload, value
for value."
```

---

### Task 3: Range on the five bespoke endpoints

**Files:**
- Modify: `pypsa-gui/backend/routers/results.py`
- Modify: `pypsa-gui/backend/tests/test_results_range.py`

**Interfaces:**
- Consumes: `_slice_ts` (imported in Task 2), `_ts_payload(..., range_meta=)`.
- Produces: nothing further; this completes the backend.

**The five, with the exact line each returns from:**

| endpoint | line | current return | `source` param? |
|---|---|---|---|
| `/unit_commitment` | 2064 | composite; `status_payload = _ts_payload(grid)` | no |
| `/prices` | 2348 | `return _ts_payload(df, extra={...})` | yes, `"lopf"` |
| `/curtailment` | 2676 | `return _ts_payload(curtailment)` | no |
| `/lost_load` | 2798 | `return _ts_payload(df, extra={...})` | no |
| `/loads` | 2929 | `return _ts_payload(df)` | yes, `"lopf"` |

- [ ] **Step 1: Extend the contract test**

In `pypsa-gui/backend/tests/test_results_range.py`, add below `SERVE_TS_ENDPOINTS`:

```python
# Bespoke bodies — they call slice_ts directly rather than via _serve_ts.
BESPOKE_ENDPOINTS = [
    "/api/results/prices",
    "/api/results/curtailment",
    "/api/results/lost_load",
    "/api/results/loads",
]

# /unit_commitment is a COMPOSITE: {generators, status_grid, n_committable},
# and only `status_grid` carries index/columns/data. Its range block lives
# inside status_grid, so the shared assertions cannot address it.
COMPOSITE_ENDPOINT = "/api/results/unit_commitment"

ALL_SERIES_ENDPOINTS = SERVE_TS_ENDPOINTS + BESPOKE_ENDPOINTS
```

Change every existing `@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)` to `ALL_SERIES_ENDPOINTS`, and add:

```python
def test_the_endpoint_list_covers_every_series_endpoint():
    """
    Guards against a seventeenth series endpoint being added and silently
    escaping this contract. Mirrors the SURFACES matrix from the
    trustworthy-numbers work: the list is the test, not a comment.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "results.py"
    text = src.read_text()
    declared = set(re.findall(r'@results_router\.get\("([^"]+)"', text))

    ranged = {u.removeprefix("/api/results") for u in ALL_SERIES_ENDPOINTS}
    ranged.add(COMPOSITE_ENDPOINT.removeprefix("/api/results"))

    # The twelve aggregate endpoints: a snapshot range is meaningless for a
    # scalar or a per-asset roll-up, so they are excluded BY NAME. Adding a
    # new endpoint forces a deliberate choice between the two lists.
    aggregates = {
        "/cost_breakdown", "/objective_decomposition", "/economics_by_carrier",
        "/statistics", "/lcoh", "/ac_pf/status", "/losses", "/carrier_kpis",
        "/emissions", "/line_duals", "/price_drivers", "/asset_economics",
    }

    unclassified = declared - ranged - aggregates
    assert not unclassified, (
        f"unclassified /results endpoints: {sorted(unclassified)} — add each to "
        f"ALL_SERIES_ENDPOINTS (if it returns index/columns/data) or to "
        f"`aggregates` (if it does not)"
    )


def test_unit_commitment_carries_its_range_inside_status_grid(solved_client):
    r = solved_client.get(COMPOSITE_ENDPOINT, params={"from": 0, "to": 0})
    assert r.status_code in (200, 204), r.text[:400]
    if r.status_code == 204:
        pytest.skip("no committable units on this fixture network")

    body = r.json()
    grid = body.get("status_grid")
    if grid is None:
        pytest.skip("no status grid on this fixture network")

    assert "range" not in body, "range belongs beside index/columns/data, not at the top"
    assert grid["range"]["from"] == 0
    assert grid["range"]["to"] == 0
    assert len(grid["data"]) == 1
```

- [ ] **Step 2: Run to verify the new assertions fail**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_results_range.py -v > /tmp/r3.log 2>&1; echo "EXIT=$?"
```

Expected: the four bespoke endpoints fail the `range`-block assertions; `test_the_endpoint_list_covers_every_series_endpoint` should PASS already (it is a classification guard, not a behaviour test) — if it fails, the endpoint inventory has drifted and you should report exactly which paths are unclassified before changing anything.

- [ ] **Step 3: Add the parameters to the four simple bespoke endpoints**

Each follows the same shape. `/loads` (`:2929`) in full:

```python
@results_router.get("/loads")
def get_load_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    # ... existing docstring unchanged ...
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        df = lp_scaled_load_frame(n, _state.get("solver_config"), source)
        if df is None or df.empty:
            return _not_solved()
        range_meta = None
        if from_ is not None or to_ is not None:
            df, range_meta = _slice_ts(df, from_, to_)
        return _ts_payload(df, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()
```

For `/prices` (`:2348`) and `/lost_load` (`:2798`), which already pass `extra={...}`, keep that argument and add `range_meta=range_meta` alongside it:

```python
        return _ts_payload(df, extra={...unchanged...}, range_meta=range_meta)
```

`/curtailment` (`:2676`) takes no `source`; add only the two `Query` parameters and slice `curtailment` before `return _ts_payload(curtailment, range_meta=range_meta)`.

In every case the slice happens **immediately before** the payload call, after the frame is fully built — so per-period scaling, price correction and curtailment arithmetic all still run over the whole frame and only the serialised rows are reduced.

- [ ] **Step 4: Add the parameters to `/unit_commitment`**

`/unit_commitment` (`:2064`) takes no `source`. Add the two `Query` parameters, and slice `grid` immediately before the existing `status_payload = _ts_payload(grid)` line:

```python
        range_meta = None
        if from_ is not None or to_ is not None:
            grid, range_meta = _slice_ts(grid, from_, to_)
        status_payload = _ts_payload(grid, range_meta=range_meta)
```

The surrounding `return {...}` composite is unchanged — `generators` is per-asset, not per-snapshot, and `n_committable` is a scalar. Neither is affected by slicing.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests tests/test_results_range.py -v > /tmp/r3.log 2>&1; echo "EXIT=$?"
```

Expected: `EXIT=0`. Report which endpoints skipped and why.

- [ ] **Step 6: Full backend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests > /tmp/r3full.log 2>&1; echo "PYTEST_EXIT=$?"; tail -4 /tmp/r3full.log
```

Expected: `PYTEST_EXIT=0`. The baseline before this plan was **2057 passed, 1 skipped**. If a failure appears in a file this plan does not touch, check `git status --porcelain` — another session shares this worktree — and report ownership rather than fixing it.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/backend/routers/results.py pypsa-gui/backend/tests/test_results_range.py -m "feat(gui): snapshot range on the five bespoke result endpoints

Completes the sixteen series endpoints. The slice happens immediately before
serialisation, after each frame is fully built, so load scaling, price
correction and curtailment arithmetic still run over the whole frame.

/unit_commitment is a composite: its range block sits inside status_grid,
beside the index/columns/data it describes.

A classification test fails if a seventeenth /results endpoint appears in
neither the series list nor the aggregate list."
```

---

### Task 4: The chunking module

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/chunking.ts`
- Create: `pypsa-gui/frontend/src/pages/results/chunking.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces, for Task 6: `CHUNK_STEPS`, `TARGET_CHUNK_BYTES`, `BYTES_PER_VALUE`, `chooseChunk(assetCount, totalSnapshots): number`, `chunkBounds(idx, chunk, total, clampTo?): {from, to}`.

- [ ] **Step 1: Write the failing tests**

Create `pypsa-gui/frontend/src/pages/results/chunking.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { chooseChunk, chunkBounds, CHUNK_STEPS } from './chunking'

describe('chooseChunk', () => {
  // Sizes derived from the measured ~10 bytes per serialised value against a
  // 512 KB target. 200 assets independently lands on a week, which is the
  // value a human would have picked — the other rows are why it is computed.
  it('picks a month for narrow networks', () => {
    expect(chooseChunk(20, 26280)).toBe(720)
    expect(chooseChunk(50, 26280)).toBe(720)
  })

  it('picks a week at 200 assets', () => {
    expect(chooseChunk(200, 26280)).toBe(168)
  })

  it('picks a day for wide networks', () => {
    expect(chooseChunk(2000, 26280)).toBe(24)
  })

  it('never goes below a day, even absurdly wide', () => {
    // A sub-day chunk would fetch a fragment of a diurnal cycle, which is the
    // unit every dispatch pattern in this domain is built on.
    expect(chooseChunk(100000, 26280)).toBe(CHUNK_STEPS[0])
    expect(chooseChunk(100000, 26280)).toBe(24)
  })

  it('never exceeds the horizon', () => {
    expect(chooseChunk(20, 100)).toBe(100)
    expect(chooseChunk(200, 10)).toBe(10)
  })

  it('falls back to a week when the asset count is unknown', () => {
    expect(chooseChunk(0, 26280)).toBe(168)
    expect(chooseChunk(-5, 26280)).toBe(168)
  })
})

describe('chunkBounds', () => {
  it('aligns to chunk boundaries so the cache key is stable', () => {
    // Every index inside one chunk must produce identical bounds, or React
    // Query refetches on every scrub step.
    const a = chunkBounds(168, 168, 26280)
    const b = chunkBounds(200, 168, 26280)
    const c = chunkBounds(335, 168, 26280)

    expect(a).toEqual({ from: 168, to: 335 })
    expect(b).toEqual(a)
    expect(c).toEqual(a)
  })

  it('produces adjacent non-overlapping chunks across a boundary', () => {
    const before = chunkBounds(335, 168, 26280)
    const after = chunkBounds(336, 168, 26280)

    expect(before.to + 1).toBe(after.from)
  })

  it('clamps the final chunk to the last row', () => {
    expect(chunkBounds(26279, 168, 26280)).toEqual({ from: 26208, to: 26279 })
  })

  it('respects a period clamp so a chunk cannot cross into another period', () => {
    // Multi-period: period 2 occupies rows 8760..17519. A chunk starting near
    // its end must stop at 17519, not spill into period 3.
    const bounds = chunkBounds(17500, 168, 26280, { start: 8760, end: 17519 })

    expect(bounds.to).toBe(17519)
    expect(bounds.from).toBeGreaterThanOrEqual(8760)
  })

  it('aligns relative to the clamp start, not to zero', () => {
    // Otherwise the first chunk of a period would be a short offcut.
    const bounds = chunkBounds(8760, 168, 26280, { start: 8760, end: 17519 })

    expect(bounds).toEqual({ from: 8760, to: 8927 })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/chunking.test.ts > /tmp/r4.log 2>&1; echo "EXIT=$?"
```

Expected: `Failed to resolve import "./chunking"`.

- [ ] **Step 3: Write the module**

Create `pypsa-gui/frontend/src/pages/results/chunking.ts`:

```ts
/**
 * How much of a result series to fetch at a time.
 *
 * The canvas overlay needs ONE row — the snapshot the scrubber sits on — but
 * playback walks the rows, so a request per frame would be worse than one big
 * download. Instead we fetch a window aligned to a fixed boundary: scrubbing
 * inside it costs nothing, crossing it costs exactly one request, and
 * scrubbing back is a cache hit.
 *
 * The window is sized by BYTES, not by a fixed number of snapshots. 168
 * snapshots of 20 assets is a wastefully small request; 168 of 2000 assets is
 * too big. Deriving it from the asset count self-selects day / week / month.
 */

/** Day, week, month, year — calendar units, so boundaries stay meaningful. */
export const CHUNK_STEPS = [24, 168, 720, 8760] as const

/** Target serialised size of one chunk. */
export const TARGET_CHUNK_BYTES = 512_000

/**
 * Measured, not guessed: a frame of the exact shape `ts_payload` emits,
 * 5,256,000 values, serialised to 52.6 MB — about 10 bytes per value.
 */
export const BYTES_PER_VALUE = 10

const DEFAULT_CHUNK = 168

/**
 * Largest calendar step whose payload fits the byte target.
 *
 * Stable by construction: it depends only on `assetCount`, which is fixed for
 * a solved network. Recomputing it as data changed would shift cache keys
 * underneath the user and destroy the hit rate that makes playback smooth.
 */
export function chooseChunk(assetCount: number, totalSnapshots: number): number {
  const horizon = Math.max(1, totalSnapshots)
  if (assetCount <= 0) return Math.min(DEFAULT_CHUNK, horizon)
  const ideal = TARGET_CHUNK_BYTES / (assetCount * BYTES_PER_VALUE)
  const fit = [...CHUNK_STEPS].reverse().find(step => step <= ideal) ?? CHUNK_STEPS[0]
  return Math.min(fit, horizon)
}

/**
 * The aligned window containing `idx`, inclusive at both ends.
 *
 * `clampTo` is the investment period the scrubber is confined to, when there
 * is one. Alignment is relative to `clampTo.start` rather than to zero, so the
 * first chunk of a period is a full chunk instead of a short offcut.
 */
export function chunkBounds(
  idx: number,
  chunk: number,
  total: number,
  clampTo?: { start: number; end: number },
): { from: number; to: number } {
  const lo = clampTo?.start ?? 0
  const hi = clampTo?.end ?? total - 1
  const safeChunk = Math.max(1, chunk)
  const offset = Math.floor((Math.max(lo, idx) - lo) / safeChunk) * safeChunk
  const from = Math.max(lo, lo + offset)
  return { from, to: Math.min(from + safeChunk - 1, hi) }
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/results/chunking.test.ts > /tmp/r4.log 2>&1; echo "EXIT=$?"
npx tsc --noEmit -p tsconfig.json > /tmp/r4tsc.log 2>&1; echo "TSC=$?"
```

Expected: `EXIT=0` with 12 passed, `TSC=0`.

- [ ] **Step 5: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git add pypsa-gui/frontend/src/pages/results/chunking.ts pypsa-gui/frontend/src/pages/results/chunking.test.ts
git commit pypsa-gui/frontend/src/pages/results/chunking.ts pypsa-gui/frontend/src/pages/results/chunking.test.ts -m "feat(gui): size result fetches by bytes rather than by a fixed window

168 snapshots of 20 assets is a wastefully small request and 168 of 2000 is
too big, so the chunk derives from the asset count against a byte target.
At 200 assets it independently lands on a week.

Bounds align to a boundary relative to the period start, so scrubbing inside
a chunk costs no requests and the first chunk of a period is a full chunk
rather than a short offcut."
```

---

### Task 5: Range-aware API client

**Files:**
- Modify: `pypsa-gui/frontend/src/api/simulation.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, for Task 6: `export interface TSRange { from: number; to: number }`, and an optional `range?: TSRange` second argument on the nine getters the canvas uses.

- [ ] **Step 1: Add the range type and helper**

In `pypsa-gui/frontend/src/api/simulation.ts`, replace `srcParam` at `:141`:

```ts
export interface TSRange { from: number; to: number }

/**
 * Query params for a time-series result request.
 *
 * Omitting `range` produces exactly the request shape that existed before
 * ranges: no `from`, no `to`, and a response with no `range` block. That is
 * what lets unconverted callers keep working untouched.
 */
const tsParams = (s?: ResultSource, range?: TSRange) => {
  const params: Record<string, string | number> = {}
  if (s) params.source = s
  if (range) { params.from = range.from; params.to = range.to }
  return Object.keys(params).length > 0 ? { params } : undefined
}
```

- [ ] **Step 2: Widen the nine getters the canvas uses**

These nine, and only these nine (the Results tabs are a separate follow-up):
`getGeneratorResults`, `getLoadResults`, `getLineResults`, `getLinkResults`,
`getLineReactive`, `getStorageDispatchResults`, `getStoreDispatchResults`,
`getStorageResults`, `getStoreEnergyResults`.

Each gains an optional second argument. `getGeneratorResults` in full:

```ts
  getGeneratorResults: (source?: ResultSource, range?: TSRange) =>
    client.get('/results/generators', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
```

Leave every other getter calling `tsParams(source)` — a one-argument call behaves exactly as `srcParam` did.

- [ ] **Step 3: Verify nothing broke**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc --noEmit -p tsconfig.json > /tmp/r5tsc.log 2>&1; echo "TSC=$?"
npx vitest run > /tmp/r5.log 2>&1; echo "VITEST=$?"
```

Expected: `TSC=0`, `VITEST=0`. Every existing call site passes one argument or none, so this is source-compatible; a type error here means a getter's signature was changed rather than widened.

- [ ] **Step 4: Confirm `srcParam` has no remaining callers**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
grep -rn "srcParam" src/ || echo "(none — fully replaced)"
```

If any remain, they were missed in Step 2; convert them to `tsParams` rather than keeping two helpers.

- [ ] **Step 5: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/frontend/src/api/simulation.ts -m "feat(gui): let time-series result getters take a snapshot range

tsParams replaces srcParam. A one-argument call emits exactly the request
that existed before, so unconverted callers are source-compatible and
receive an unchanged response."
```

---

### Task 6: Chunked canvas overlay

**Files:**
- Modify: `pypsa-gui/frontend/src/components/CanvasResultsContext.tsx`

**Interfaces:**
- Consumes: `chooseChunk`, `chunkBounds` (Task 4); `TSRange` and the nine widened getters (Task 5); the backend `range` block (Tasks 2–3).
- Produces: nothing further.

**The defect this task must not introduce.** The existing row lookup is:

```ts
const len = ts?.data.length ?? ... ?? 0
const idx = Math.max(0, Math.min(resultsSnapshotIdx, len - 1))
const gMap = rowMap(ts, idx)
```

Under chunking `len` is the CHUNK length, not the horizon. Left as-is, asking for snapshot 5000 would clamp to 167 and render row 167's flows **as if they were snapshot 5000's** — a confident wrong number on the canvas, which is precisely the failure class this codebase has been eliminating. The clamp must use `range.total` and the lookup must subtract `range.from`.

- [ ] **Step 1: Add the imports and the payload range type**

At the top of `CanvasResultsContext.tsx`, add:

```ts
import { chooseChunk, chunkBounds } from '../pages/results/chunking'
import type { TSRange } from '../api/simulation'
```

`TSRange` must be exported from `simulation.ts` by Task 5; if it is not, that is a Task 5 gap — report it rather than redeclaring the type here, because two definitions of the same wire shape is how they drift.

Then extend the local `TSPayload` type (or import it) so `range` is visible:

```ts
// Present only on ranged responses. Absent means the payload is the whole
// series — the pre-range shape, which unconverted callers still receive.
interface TSRangeMeta {
  from: number
  to: number
  total: number
  complete: boolean
  capped: boolean
}
```

and add `range?: TSRangeMeta` to the payload type this file uses.

- [ ] **Step 2: Derive the chunk per endpoint**

The nine endpoints have different widths — `line_reactive` is not as wide as `generators` — so one shared chunk size would size every request by whichever endpoint was probed first. Add, before the queries:

```ts
  // Probe: one row, but the response carries the full `columns` array and
  // `range.total`. A few KB, and it reuses the parameter we already added
  // rather than needing a new endpoint.
  const probeRange = useMemo<TSRange>(() => ({ from: 0, to: 0 }), [])

  const { data: gensProbe } = useQuery({
    queryKey: nk(currentProject, 'results', 'generators', resultSource, 'probe'),
    queryFn: () => resultsApi.getGeneratorResults(resultSource, probeRange),
    enabled: enableQueries,
    staleTime: Infinity,
  })

  const total = gensProbe?.range?.total ?? 0
  const gensChunk = useMemo(
    () => chooseChunk(gensProbe?.columns.length ?? 0, total),
    [gensProbe?.columns.length, total],
  )
  const gensBounds = useMemo(
    () => chunkBounds(resultsSnapshotIdx, gensChunk, total, activeRangeClamp),
    [resultsSnapshotIdx, gensChunk, total, activeRangeClamp],
  )
```

Repeat the probe/chunk/bounds trio for each of the nine endpoints, each using its own `columns.length`. Where an endpoint's probe returns 204 (no data on this network), its bounds fall back to `{from: 0, to: 0}` and the main query stays disabled — `enabled: enableQueries && total > 0`.

**The cost this accepts, stated rather than hidden.** Nine probes plus nine chunk
queries is eighteen requests where there were nine. Each probe is a single row —
a few KB — and `staleTime: Infinity` means it is fetched once per project and
never refetched. There is also one genuine duplicate: when the scrubber sits at
index 0 the probe and the main query request the same rows under different cache
keys. Both are accepted deliberately: eighteen small requests beat nine
multi-megabyte ones, and removing the probe would mean guessing a chunk size and
then changing it once the first response arrives — which shifts every cache key
underneath the user and refetches everything. If probe count ever matters, the
fix is one batched metadata endpoint returning `{columns_count, total}` per
series, not deriving the chunk after the fact.

`activeRangeClamp` is the investment-period window, or `undefined` on a flat network:

```ts
  // SnapshotPicker's activeRange is the WHOLE range on a flat network, so it
  // is useless as the window — but as a clamp it stops a chunk crossing an
  // investment-period boundary on multi-period networks.
  const activeRangeClamp = useMemo(
    () => (periodRange ? { start: periodRange.start, end: periodRange.end } : undefined),
    [periodRange],
  )
```

Read how `SnapshotPicker.tsx:96` derives `activeRange` from `periodInfo` and reuse that source rather than duplicating the logic; if it is not exported, lift it into a shared helper and import it in both places.

- [ ] **Step 3: Make each query chunk-aware**

For each of the nine, add the chunk start to the key and pass the bounds:

```ts
  const { data: gensTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'generators', resultSource, gensBounds.from),
    queryFn: () => resultsApi.getGeneratorResults(resultSource, gensBounds),
    enabled: enableQueries && total > 0,
  })
```

The trailing `gensBounds.from` is what makes the cache work: identical inside a chunk, different across one.

- [ ] **Step 4: Fix the row lookup**

Replace the clamp and lookup block at `:236-244`:

```ts
    // `range.total` is the HORIZON; `data.length` is only this chunk. Clamping
    // against the chunk would silently render the wrong snapshot's flows.
    const horizon = ts?.range?.total ?? lts?.range?.total ?? ts?.data.length ?? 0
    if (horizon === 0) return EMPTY
    const globalIdx = Math.max(0, Math.min(resultsSnapshotIdx, horizon - 1))

    // Each payload may sit on a different chunk, so offset per payload rather
    // than once. `rowMap` returns null for an out-of-chunk index, which renders
    // nothing — the safe failure — instead of a neighbouring row's numbers.
    const localIdx = (p: typeof ts) => globalIdx - (p?.range?.from ?? 0)
    const iso = ts?.index[localIdx(ts)] ?? lts?.index[localIdx(lts)] ?? ''

    const gMap = rowMap(ts, localIdx(ts))
    const lMap = rowMap(lts, localIdx(lts))
```

Apply the same `localIdx(...)` treatment to every remaining `rowMap(x, idx)` call in the block.

- [ ] **Step 5: Verify**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc --noEmit -p tsconfig.json > /tmp/r6tsc.log 2>&1; echo "TSC=$?"
npx vitest run > /tmp/r6.log 2>&1; echo "VITEST=$?"
```

Expected: `TSC=0`, `VITEST=0`.

- [ ] **Step 6: Prove the offset is right**

Add to `chunking.test.ts` a test of the offset arithmetic itself, since it is the step most likely to be subtly wrong:

```ts
it('maps a global snapshot index into its chunk-local row', () => {
  // Snapshot 200 lives in chunk [168, 335] at local row 32.
  const bounds = chunkBounds(200, 168, 26280)
  const local = 200 - bounds.from

  expect(local).toBe(32)
  expect(bounds.from + local).toBe(200)
})
```

Re-run vitest and confirm it passes.

- [ ] **Step 7: Commit**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
git branch --show-current
git commit pypsa-gui/frontend/src/components/CanvasResultsContext.tsx pypsa-gui/frontend/src/pages/results/chunking.test.ts -m "feat(gui): fetch canvas overlay results in aligned chunks

Nine full series were downloaded to read one row from each - about 470 MB on
a 200-asset three-period network. Each query now fetches the aligned chunk
containing the scrubber, sized per endpoint from its own column count.

The row lookup clamps against range.total and offsets by range.from. Left
clamping against data.length, asking for snapshot 5000 would have rendered
row 167's flows as if they were snapshot 5000's."
```

---

## Final verification

- [ ] **Full backend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur"
pixi run gui-tests > /tmp/rfull.log 2>&1; echo "PYTEST_EXIT=$?"; tail -4 /tmp/rfull.log
```

Expected `PYTEST_EXIT=0`. Baseline before this plan: **2057 passed, 1 skipped**.

- [ ] **Full frontend suite**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/frontend"
export PATH="/Users/orange/Desktop/Code Test/pypsa-eur/.pixi/envs/default/bin:$PATH"
npx tsc --noEmit -p tsconfig.json > /tmp/rfulltsc.log 2>&1; echo "TSC=$?"
npx vitest run > /tmp/rfullfe.log 2>&1; echo "VITEST=$?"
```

Expected `TSC=0`, `VITEST=0`. Baseline: **470 passed / 62 files**.

- [ ] **Rebuild the DMG**

```bash
cd "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui"
bash build-macos.sh > /tmp/rbuild.log 2>&1; echo "BUILD_EXIT=$?"; tail -12 /tmp/rbuild.log
```

Expected `BUILD_EXIT=0`, a clean secret-scan line, and a DMG timestamp later than the last commit. Per CLAUDE.md the desktop app serves the BUILT SPA from the gitignored `frontend/dist/`, so until this runs the change exists in source only.

---

## Notes for the executor

- **Do not convert the Results tabs.** They already window client-side and already produce correct totals. Converting them would put all 85 `weightedSum` call sites in the blast radius of a payload change, and it needs the `complete` flag wired into anything rendering a horizon total. That is a separate plan.
- **Do not add downsampling**, even as an unused parameter. See the Global Constraints.
- **A response with no `from`/`to` must never gain a `range` key.** Several tests assert this, and it is what keeps the unconverted consumers working.
- **`from` is a Python keyword** — `Query(None, alias="from")` is mandatory, and `from_` is the parameter name.
- **The chunk floor and the server cap can collide at extreme widths, and the
  failure is safe.** `chooseChunk` never returns less than a day, so a
  100,000-asset network asks for 24 × 100,000 = 2,400,000 values — above
  `MAX_RESPONSE_VALUES`. The server caps `to`, sets `capped: true`, and returns
  fewer rows than the client's chunk assumed. The offset lookup still uses
  `range.from`, which is unchanged, so indices past the served rows make
  `rowMap` return `null` and the canvas renders nothing for them rather than a
  neighbouring row's numbers. Nothing to fix; verified by reading the
  arithmetic, and recorded so the next person does not read it as a bug. A
  network that wide needs column selection, which is out of scope here.
