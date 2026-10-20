"""The flow files eh_readiness's uncoded warnings and the templates' ledger
rows into named sections, with no silent fallback (B1, gatekeeper point 4).

eh_readiness's `warnings[]` are sentences without a code, so the flow files
them by the prefixes in `flow_service.READINESS_WARNING_PREFIXES`. This drives
every template (and the variants that make eh_readiness say more: VOLL 0, an
energy-import cap, a pack it cannot apply) and fails on a sentence no prefix
matches: at runtime such a sentence files to `readiness` (`eh_readiness_note`),
which a shipped template must never reach. Each typed refusal class becomes one
connection item, never a failed endpoint. The same for the ledger: every
`assumed` row of a shipped template matches a word of `_LEDGER_SECTIONS`.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from services import flow_service as S

TEMPLATES = ["eh_datacenter", "eh_datacenter_staged", "eh_h2_hub", "eh_h2_hub_staged",
             "eh_microgrid", "eh_microgrid_staged", "eh_site"]


def _create(client, tid, name="filing"):
    kw = {"json": {"site_type": "industrial"}} if tid == "eh_site" else {}
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name}, **kw)
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _readiness_items(body):
    return [i for s in body["sections"] for i in s["items"] if i["source"] == "eh_readiness"]


def _cfg(client, **kw):
    cfg = client.get("/api/simulation/solver_config").json()
    assert client.put("/api/simulation/solver_config", json={**cfg, **kw}).status_code == 200


VARIANTS = {
    "as_created": lambda client, d: None,
    "voll_zero": lambda client, d: _cfg(client, voll=0.0),
    # weak_flexible: an energy-import cap makes eh_readiness note the metered Links.
    "energy_cap": lambda client, d: _override(d, import_energy_mwh_per_year=1000.0),
    # A pack it cannot apply (an energy cap on a two-way import Link):
    # "pack cannot be applied".
    "pack_refused": lambda client, d: (_override(d, import_energy_mwh_per_year=1000.0),
                                       _two_way(client)),
}


def _two_way(client):
    links = {r["name"]: r for r in client.get("/api/network/links").json()}
    if "grid_import" in links:
        r = client.put("/api/network/links/grid_import",
                       json={**links["grid_import"], "p_min_pu": -1.0})
        assert r.status_code == 200, r.text


def _override(project_dir: pathlib.Path, **kw):
    path = project_dir / "eh_template.json"
    meta = json.loads(path.read_text())
    po = dict(meta.get("pack_overrides") or {})
    for k, v in kw.items():
        if v is None:
            po.pop(k, None)
        else:
            po[k] = v
    meta["pack_overrides"] = po
    path.write_text(json.dumps(meta))


@pytest.mark.parametrize("variant", sorted(VARIANTS))
@pytest.mark.parametrize("tid", TEMPLATES)
def test_every_readiness_warning_files_to_a_named_section(
        client, tmp_projects_dir, project_storage_dir, tid, variant):
    name = _create(client, tid)
    VARIANTS[variant](client, project_storage_dir(name))
    body = client.get(f"/api/projects/{name}/flow").json()
    unfiled = [i["message"] for i in _readiness_items(body) if i["code"] == "eh_readiness_note"]
    assert not unfiled, f"add a prefix to READINESS_WARNING_PREFIXES for: {unfiled}"
    for i in _readiness_items(body):
        assert i["target"]["section"] in S.SECTION_IDS_INPUT, i


def test_the_variants_reach_every_prefix(client, tmp_projects_dir, project_storage_dir):
    """So the test above exercises each sentence family at least once."""
    seen: set[str] = set()
    real = S.readiness_warning_filing

    def spy(text):
        out = real(text)
        if out is not None:
            seen.add(out[0])
        return out
    S.readiness_warning_filing = spy
    try:
        _reach(client, project_storage_dir)
    finally:
        S.readiness_warning_filing = real
    assert {code for _, code, _ in S.READINESS_WARNING_PREFIXES} <= seen, seen


def _reach(client, project_storage_dir):
    for tid, variant in (("eh_h2_hub", "voll_zero"), ("eh_datacenter", "energy_cap"),
                         ("eh_datacenter", "pack_refused")):
        name = _create(client, tid, name=f"reach_{tid}_{variant}")
        VARIANTS[variant](client, project_storage_dir(name))
        S._readiness_cache.clear()
        assert client.get(f"/api/projects/{name}/flow").status_code == 200


def test_an_unknown_sentence_files_to_readiness_not_connection():
    items = S._readiness_items({"archetype": "strong_grid", "import": {"links": ["g"]},
                                "critical_buses": ["b"], "warnings": ["something new"]},
                               voll_noted=False)
    assert [(i["code"], i["target"]["section"]) for i in items] == [
        ("eh_readiness_note", "readiness")]


def test_each_refusal_class_is_one_connection_item(client, tmp_projects_dir, monkeypatch):
    from services.adequacy.eh_study import readiness_refusal_classes

    name = _create(client, "eh_h2_hub")
    for cls in readiness_refusal_classes():
        S._readiness_cache.clear()

        def refuse(*a, _cls=cls, **k):
            raise _cls("refused for the test")
        monkeypatch.setattr("services.adequacy.eh_readiness.eh_readiness", refuse)
        r = client.get(f"/api/projects/{name}/flow")
        assert r.status_code == 200, (cls, r.text)
        items = _readiness_items(r.json())
        assert [(i["code"], i["target"]["section"]) for i in items] == [
            ("eh_readiness_refused", "connection")], cls
        assert items[0]["blocking"] is False                 # the study's own refusal blocks


def _ledgers() -> dict[str, list[dict]]:
    tpl = pathlib.Path(__file__).resolve().parent.parent / "project_templates"
    out = {p.parent.name: json.loads(p.read_text()).get("ledger") or []
           for p in sorted(tpl.glob("eh_*/eh_template.json"))}
    from project_templates import eh_site as E

    for t in E.SITE_TYPES:
        for ride in (False, True):
            meta = E.template_meta(E.site_params({"site_type": t, "ride_through": ride}))
            out[f"eh_site:{t}:{ride}"] = meta.get("ledger") or []
    return out


@pytest.mark.parametrize("tid", sorted(_ledgers()))
def test_every_assumed_ledger_row_files_to_a_named_section(tid):
    rows = [r for r in _ledgers()[tid] if r.get("source") == "assumed"]
    unfiled = [r["item"] for r in rows if S.ledger_section(r["item"]) is None]
    assert not unfiled, f"add a word to _LEDGER_SECTIONS for: {unfiled}"
