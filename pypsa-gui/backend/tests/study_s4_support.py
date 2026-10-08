"""Shared fixtures and helpers for the S4 decision-study tests."""
from __future__ import annotations

import hashlib
import pathlib
import time

import main
from routers import studies as studies_router

INTAKE = {
    "site": {"zone": "DE", "connection_mw": 2.0},
    "tariff": {"tariff_id": "de_industrial_illustrative"},
    "load": {"source": "sector_profile", "profile": "commercial_office",
             "annual_mwh": 4000.0},
    "pv": {"enabled": True, "kind": "rooftop"},
}
OPTIONS = ["none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h"]


def enable_studies(monkeypatch):
    """Generator body of each test module's `studies_on` fixture."""
    monkeypatch.setenv("PYPSAGUI_DECISION_STUDIES", "1")
    dep = studies_router.require_decision_studies_enabled
    main.app.dependency_overrides[dep] = lambda: None
    monkeypatch.setattr(studies_router, "require_decision_studies_enabled", lambda: None)
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(dep, None)


def dir_hash(path: pathlib.Path) -> str:
    """Every file's relative path and bytes, in order."""
    h = hashlib.sha256()
    for f in sorted(p for p in pathlib.Path(path).rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def all_project_dirs(session_local) -> dict[str, pathlib.Path]:
    from sqlalchemy import select

    from db.models import Project
    from services import project_registry

    with session_local() as db:
        return {str(p.id): project_registry.project_dir(p)
                for p in db.scalars(select(Project)).all()}


def create_pack_study(client, path_project: str, base: str, intake=None,
                      name="Site battery"):
    r = client.post(f"/api/projects/{path_project}/studies/", json={
        "question_id": "bess_at_site", "name": name, "project_name": base,
        "intake": INTAKE if intake is None else intake})
    return r


def wait_run(client, base: str, study_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/projects/{base}/studies/{study_id}/run")
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("the study run did not finish")


def record_engine_solve(n, commercial):
    """
    What the engine's solve leaves on a network (U2 WP8), for the fake
    solvers: the PoC prices materialised from the fork's commercial config,
    then undone and COMMITTED as `run_simulation` does (the PoC record that
    makes the fork `engine_ready` and its bills established), and the LP
    terms' records — the fake has no LP model for `lp_bindings._read_demand_
    solution` / `_read_capacity_solution` to read, so each demand key's and
    each annual capacity key's peak is read from the written import dispatch
    (hourly settlement: one snapshot per interval), the way those readers
    read the solved one, and a contracted capacity on the fixed PoC is the
    constant `add_capacity_terms` records. Returns the site's import price
    per snapshot (what the fake writes as the bus price). The dispatch
    (`links_t.p0`) must be written first.
    """
    from services.commercial import connection
    from services.commercial import lp_bindings as lp

    applied = lp.materialise_poc_prices(n, commercial)
    spec = getattr(n, lp.DEMAND_SPEC_ATTR, None)
    cap = getattr(n, lp.CAPACITY_SPEC_ATTR, None)
    try:
        site_price = n.links_t.marginal_cost["grid_import"].copy()
    finally:
        applied.undo()
    applied.commit()
    if spec or cap:
        links = (spec or cap)["import_links"]
        flow = n.links_t.p0[links].sum(axis=1).to_numpy(dtype=float)

        def peak(pos) -> float:
            return max(0.0, float(flow[pos].max()))
    if spec:
        n.meta[lp.META_DEMAND] = {
            k["key"]: {"item": k["item"], "period": k["period"], "month": k["month"],
                       "inv_period": k["inv_period"], "eur_per_mw": k["eur_per_mw"],
                       "peak_mw": peak(k["positions"]), "billed_mw": peak(k["positions"])}
            for k in spec["keys"]}
        n.meta[lp.META_DEMAND_INFO] = spec["info"]
    if cap:
        link, fixed = cap["link"], {}
        for c in cap["contracted"]:
            assert not bool(n.links.at[link, "p_nom_extendable"]), "a fake solve sizes no PoC"
            _coef, by_period = connection.capacity_fee_coefficient(n, link, c["eur_per_mw_year"])
            p_nom = float(n.links.at[link, "p_nom"])
            fixed[c["item"]] = {"link": link, "p_nom_mw": p_nom,
                                "eur_by_period": {k: v * p_nom for k, v in by_period.items()}}
        n.meta[lp.META_CAPACITY] = {
            "contracted": {}, "fixed": fixed,
            "peaks": {k["key"]: {"item": k["item"], "inv_period": k["inv_period"],
                                 "year": k["year"], "eur_per_mw": k["eur_per_mw"],
                                 "peak_mw": peak(k["positions"])} for k in cap["peaks"]},
            "items_hash": cap["items_hash"], "hash_version": cap["hash_version"]}
    return site_price
