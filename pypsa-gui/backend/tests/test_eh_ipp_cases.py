"""
The IPP example's cases (`eh_ipp_hybrid/cases.json`; IPP renewables + BESS ticket 04, spec §7).
Data only until the sensitivities slice (S5): nothing lists or applies them yet. Pinned here:

* every change passes the experiment contract's path and value rules, within its limits;
* the shipped file is the template's own `cases()`, and the created project carries it;
* every path resolves on a project created from the template, and every `old` is its value;
* the contract S5 must meet (gatekeeper): a series `.id` resolves to its one Library entry and
  the whole reference (id, version, hash) is written; the cheap-battery parts re-derive the
  capital cost through the asset form; the tight connection caps the export at 55 MW.
"""
from __future__ import annotations

import copy
import json

import pytest

import project_templates.eh_templates as T
from harness import experiment_contract as contract
from services import experiments as X

TID = "eh_ipp_hybrid"
TPL_DIR = __import__("pathlib").Path(T.__file__).parent
DOC = T.CASES[TID]()
CHANGES = [(c["id"], ch) for c in DOC["cases"] for ch in c["changes"]]


def test_every_change_passes_the_contracts_rules():
    assert 1 <= len(DOC["cases"]) <= contract.MAX_CASES == 8
    for case in DOC["cases"]:
        assert 1 <= len(case["changes"]) <= contract.MAX_CHANGES
        assert case["reason"] and case["label"]
        for ch in case["changes"]:
            assert set(ch) == {"path", "value_type", "old", "new"}
            for side in ("old", "new"):
                X._validate_change({"path": ch["path"], "value_type": ch["value_type"],
                                    "value": ch[side]})
            assert ch["old"] != ch["new"]


def test_the_shipped_file_is_the_templates_own():
    assert json.loads((TPL_DIR / TID / T.CASES_FILE).read_text()) == json.loads(json.dumps(DOC))
    assert [c["id"] for c in DOC["cases"]] == [
        "low_price", "cannibalised", "ppa_price_minus_10", "merchant_heavy", "longer_ppa",
        "cheap_battery", "tight_connection", "faster_degradation"]


# ── on a created project ────────────────────────────────────────────────────


def _create(client):
    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "ipp"})
    assert r.status_code == 200, r.text


def _resolve(client, path: str):
    """The created project's current value at a declared path."""
    head, *rest = path.split(".")
    if head == "commercial":
        node = client.get("/api/simulation/solver_config").json()["commercial"]
        if rest[0] == "contracts":
            node, rest = next(c for c in node["contracts"] if c["id"] == rest[1]), rest[2:]
        for k in rest:
            node = node[k]
        return node
    if head == "finance":
        node = client.get("/api/simulation/finance").json()["finance"]
        for k in rest:
            node = node[k]
        return node
    name, attr = ".".join(rest[:-1]), rest[-1]
    rows = client.get(f"/api/network/{T_PATHS[head]}").json()
    return next(r for r in rows if r["name"] == name)[attr]


T_PATHS = {"Link": "links", "StorageUnit": "storage_units", "Generator": "generators"}


def test_every_path_resolves_and_every_old_is_the_projects_value(client, tmp_projects_dir,
                                                                  bound_ctx):
    from services import network_lengths

    _create(client)
    for cid, ch in CHANGES:
        got = _resolve(client, ch["path"])
        assert got == pytest.approx(ch["old"]) if ch["value_type"] == "number" \
            else got == ch["old"], (cid, ch["path"], got)
    with bound_ctx(client):
        project_dir = network_lengths.active_project_dir()
    assert json.loads((project_dir / T.CASES_FILE).read_text()) == json.loads(json.dumps(DOC))


def _resolve_ref(series: list[dict], ref: dict, new_id: str) -> dict:
    """The contract S5 must meet (gatekeeper): a `.id` change resolves the id in the org Library
    to its single entry and writes the WHOLE reference, refusing an absent or ambiguous id."""
    hits = [s for s in series if s["id"] == new_id]
    if len(hits) != 1:
        raise LookupError(f"series {new_id!r}: {len(hits)} Library entries")
    return {**ref, **{k: hits[0][k] for k in ("id", "version", "hash")}}


def test_a_series_change_writes_the_whole_library_reference(client, tmp_projects_dir):
    _create(client)
    series = client.get("/api/library/series").json()
    com = client.get("/api/simulation/solver_config").json()["commercial"]
    (low,) = [c for c in DOC["cases"] if c["id"] == "low_price"]
    for ch in low["changes"]:
        ref_path = ch["path"].removesuffix(".id")
        before = _resolve(client, ref_path)
        after = _resolve_ref(series, before, ch["new"])
        (entry,) = [s for s in series if s["id"] == ch["new"]]
        assert (after["id"], after["version"], after["hash"]) == (
            entry["id"], entry["version"], entry["hash"])
        assert after["hash"] != before["hash"]                 # never the id alone
    with pytest.raises(LookupError):
        _resolve_ref(series, com["export_price_ref"], "no_such_series")
    with pytest.raises(LookupError):
        _resolve_ref(series + [dict(series[0])], com["export_price_ref"], series[0]["id"])


def test_the_cheap_battery_rederives_the_capital_cost(client, tmp_projects_dir, bound_ctx):
    """Applied through the asset form (S5's route for these attributes): capital_cost after the
    update equals `derive.apply_parts` of the new parts."""
    import pypsa

    from services.asset_schema import derive
    from services.pypsa_service import PyPSAService

    _create(client)
    (cheap,) = [c for c in DOC["cases"] if c["id"] == "cheap_battery"]
    row = next(r for r in client.get("/api/network/storage_units").json() if r["name"] == "bess")
    new = {ch["path"].rsplit(".", 1)[1]: ch["new"] for ch in cheap["changes"]}
    body = {k: v for k, v in row.items() if not k.endswith("_opt")} | new
    assert client.put("/api/network/storage_units/bess", json=body).status_code == 200
    got = next(r for r in client.get("/api/network/storage_units").json() if r["name"] == "bess")
    with bound_ctx(client):
        ref = copy.deepcopy(PyPSAService.get_network())
    rate = float(client.get("/api/simulation/solver_config").json()["discount_rate"])
    ref.storage_units.loc["bess", "capital_cost"] = float("nan")
    derive.apply_parts(ref, "StorageUnit", "bess", new, discount_rate=rate)
    assert got["capital_cost"] == pytest.approx(float(ref.storage_units.at["bess", "capital_cost"]))
    assert got["capital_cost"] == pytest.approx(row["capital_cost"] * 0.7, rel=0.05)
    assert isinstance(ref, pypsa.Network)


@pytest.mark.live_solve
def test_the_tight_connection_caps_the_export(_module_backend):
    from tests.test_eh_ipp import _solve

    (tight,) = [c for c in DOC["cases"] if c["id"] == "tight_connection"]
    (ch,) = tight["changes"]
    n = T.BUILDERS[TID]()
    n.links.at["grid_export", "p_nom"] = ch["new"]
    n, _cfg, _da = _solve(T.solver_config(TID), network=n)
    assert float(n.links_t.p0["grid_export"].max()) <= 55.0 + 1e-6
    assert float(n.links_t.p0["grid_export"].max()) == pytest.approx(55.0, abs=1e-3)


@pytest.fixture(scope="module")
def _module_backend():
    from tests.conftest import _reset_backend_state

    _reset_backend_state()
    yield
    _reset_backend_state()
