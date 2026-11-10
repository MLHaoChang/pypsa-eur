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
             "eh_microgrid", "eh_microgrid_staged", "eh_site", "eh_ammonia_staged"]


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
        assert i["target"]["section"] in S.F.INPUT_SECTIONS, i


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
    unfiled = [r["item"] for r in rows
               if r.get("section") not in S.F.INPUT_SECTIONS
               and S.ledger_section(r["item"]) is None]
    assert not unfiled, f"add a word to _LEDGER_SECTIONS for: {unfiled}"


# DC-02's rows name their own target (`{item, value, unit, source, note,
# section, field, entity, component, panel}`).
DC02_ROWS = [
    {"item": "connection_ramp", "label": "Connection ramp limit", "value": 4.0,
     "unit": "MW/min", "source": "assumed",
     "note": "...", "section": "connection", "panel": None, "component": "Link",
     "entity": "grid_import", "field": "connection_ramp"},
    {"item": "fault_level", "value": 800.0, "unit": "MVA", "source": "assumed", "note": "...",
     "section": "connection", "panel": None, "component": "Bus", "entity": "poc",
     "field": "eh_sk_mva"},
    {"item": "oscillation", "value": 2.0, "unit": "% of connection", "source": "assumed",
     "note": "n", "section": "connection", "field": "oscillation_band", "entity": "grid_import",
     "component": "Link", "panel": None},
    {"item": "import_efficiency", "value": 0.99, "unit": None, "source": "assumed",
     "note": "n", "section": "connection", "field": "efficiency", "entity": "grid_import",
     "component": "Link", "panel": None},
    {"item": "lole_goal", "value": 0.5, "unit": "h/yr", "source": "assumed", "note": "n",
     "section": "scenarios", "field": "target_lole_h", "entity": None, "component": None,
     "panel": "simparams"},
]


def test_a_row_with_its_own_target_is_filed_there():
    items = S._assumed_items({"ledger": DC02_ROWS + [
        {"item": "discount rate", "value": "7 %", "source": "assumed"}]})
    got = {i["code"]: (i["target"], i["message"]) for i in items}
    assert got["assumed_connection_ramp"] == (
        {"section": "connection", "panel": None, "component": "Link",
         "entity": "grid_import", "field": "connection_ramp"},
        "Connection ramp limit: 4 MW/min (assumed): confirm it or set your own.")
    assert got["assumed_fault_level"] == (
        {"section": "connection", "panel": None, "component": "Bus", "entity": "poc",
         "field": "eh_sk_mva"},
        "Fault level: 800 MVA (assumed): confirm it or set your own.")    # no label: humanised
    assert len(items) == 6
    assert got["assumed_lole_goal"][0] == {"section": "scenarios", "panel": "simparams",
                                           "component": None, "entity": None,
                                           "field": "target_lole_h"}
    assert got["assumed_import_efficiency"][1].startswith("Import efficiency: 0.99 (assumed)")
    # An older row keeps today's behaviour.
    assert got["assumed_default"][0]["section"] == "commercial"
    assert got["assumed_default"][0]["entity"] == "discount rate"


def test_a_stored_goal_retires_its_assumed_row():
    """W3b-R: once the project stores its reliability goal, the template's
    assumed LOLE row no longer stands for the run, so it is not raised; the
    other rows stay."""
    items = S._assumed_items({"ledger": DC02_ROWS}, {"target_lole_h": 4.0})
    codes = {i["code"] for i in items}
    assert "assumed_lole_goal" not in codes
    assert "assumed_connection_ramp" in codes
    # No goal stored (or a config with other fields only): the row stays.
    assert "assumed_lole_goal" in {i["code"] for i in S._assumed_items({"ledger": DC02_ROWS}, {})}


def test_two_rows_on_one_entity_keep_separate_acks(tmp_path):
    items = S._assumed_items({"ledger": DC02_ROWS})
    S.acknowledge(tmp_path, items, "assumed_connection_ramp", "grid_import")
    S._apply_acks(items, S.read_acks(tmp_path))
    acked = {i["code"]: i["acknowledged"] for i in items}
    assert acked["assumed_connection_ramp"] is True
    assert acked["assumed_oscillation"] is False


def test_an_assumed_value_shows_at_full_precision():
    """No exponent form or 6-digit rounding (`1.5e+06`), and the ack value is
    the raw value, so a change past the 6th digit still re-raises it."""
    items = S._assumed_items({"ledger": [
        {"item": "fault_level", "value": 1500000, "unit": "kVA", "source": "assumed",
         "section": "connection"},
        {"item": "import_efficiency", "value": 0.9876543, "source": "assumed",
         "section": "connection"}]})
    got = {i["code"]: i for i in items}
    assert got["assumed_fault_level"]["message"].startswith("Fault level: 1500000 kVA (assumed)")
    assert got["assumed_import_efficiency"]["message"].startswith(
        "Import efficiency: 0.9876543 (assumed)")
    assert got["assumed_import_efficiency"]["_value"] == "0.9876543"


# ── ui-modern 05: the words a Guided section shows ────────────────────────────

#: Engineering keys a user-facing assumed row must not carry (audit finding 4).
RAW_KEYS = ("p_max_pu", "tau_f", "i_max", "k_gfm", "island_constraints", "inv_power_lifetime",
            "RoCoF", "sidecar")


@pytest.mark.parametrize("tid", sorted(_ledgers()))
def test_no_assumed_row_shows_an_engineering_key(tid):
    for item in S._assumed_items({"ledger": _ledgers()[tid]}):
        found = [k for k in RAW_KEYS if k in item["message"]]
        assert not found, f"{tid}: {item['message']!r} shows {found}"


def test_a_ledger_row_is_said_in_plain_words_and_keeps_its_record():
    rows = [{"item": "island rows", "value": "off (island_constraints false)", "source": "assumed"},
            {"item": "transition RoCoF limit", "value": "2 Hz/s over 500 ms", "source": "assumed"},
            {"item": "island unit data", "source": "assumed",
             "value": "diesel droop 4 %, H 1.5 s; batteries tau_f 0.1 s, i_max 1.2 pu, "
                      "k_gfm_min 1.0, pf 0.95"}]
    items = S._assumed_items({"ledger": rows})
    assert [i["message"] for i in items] == [
        "Island limits in the optimisation: off (assumed): confirm it or set your own.",
        "Rate-of-change limit during the transition: 2 Hz/s over 500 ms (assumed): "
        "confirm it or set your own.",
        "Island unit settings: diesel droop 4 %, inertia constant 1.5 s; batteries frequency "
        "filter time 0.1 s, current limit 1.2 p.u., grid-forming current at least 1.0, "
        "power factor 0.95 (assumed): confirm it or set your own."]
    # The acknowledgement binds to the ledger's own value, not to the plain words.
    assert items[0]["_value"] == "off (island_constraints false)"


def test_the_rate_of_change_word_is_the_island_panels():
    panel = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
             / "CampusIslandSection.tsx").read_text()
    assert "'Rate of change (Hz/s)'" in panel
    label, _ = S._LEDGER_PLAIN["transition RoCoF limit"]
    assert label.replace("-", " ").lower().startswith("rate of change")


CONFIG_URL = "/api/simulation/solver_config"


def _microgrid_flow(client, name: str, tid: str = "eh_microgrid") -> dict:
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name})
    assert r.status_code == 200, r.text
    return client.get(f"/api/projects/{name}/flow").json()


def _connection_defaults(flow: dict) -> tuple[dict, list[dict]]:
    conn = next(s for s in flow["sections"] if s["section"] == "connection")
    return conn, [i for i in conn["items"] if i["code"].startswith("assumed_")]


def test_an_off_grid_section_keeps_its_defaults_out_of_scope(client, tmp_projects_dir):
    """Section 3 is "not needed" off-grid: its four assumed rows stay listed (the
    audit trail) but are out of scope and counted nowhere; nothing else moves."""
    flow = _microgrid_flow(client, "mg_words")
    conn, defaults = _connection_defaults(flow)
    assert conn["state"] == "not_needed"
    assert sorted(i["code"] for i in defaults) == [
        "assumed_connection_ramp", "assumed_default", "assumed_import_efficiency",
        "assumed_oscillation"]
    assert [i["in_scope"] for i in defaults] == [False] * 4
    assert not any(i["blocking"] for i in defaults)
    by_id = {s["section"]: s for s in flow["sections"]}
    assert by_id["readiness"]["summary"] == "0 to do, 14 to check"
    # The assumed rows of the sections the project does need are still asked for.
    assert all(i["in_scope"] for s in ("assets", "commercial", "scenarios")
               for i in by_id[s]["items"] if i["code"].startswith("assumed_"))
    assert flow["run_ready"] is True and flow["run_blocker"] is None
    assert flow["verdicts"]["optimisation"] == {"ready": True, "blocker": None}


def test_the_staged_microgrid_keeps_its_feeder_losses_counted(client, tmp_projects_dir):
    """The staged island files a fifth row to section 3, the hospital feeder's
    losses: that link carries flow, so its default is still asked for."""
    flow = _microgrid_flow(client, "mg_staged", "eh_microgrid_staged")
    conn, defaults = _connection_defaults(flow)
    assert conn["state"] == "not_needed"
    scope = {i["target"]["entity"]: i["in_scope"] for i in defaults}
    assert scope == {"hospital feeder losses": True, "point of connection": False,
                     "subsea_tie": False}
    assert len(defaults) == 5
    readiness = next(s for s in flow["sections"] if s["section"] == "readiness")
    assert readiness["summary"] == "0 to do, 11 to check"


def _row(code: str, entity: str, severity: str = "warning") -> dict:
    return S._item(code, severity, "m", "template", "connection", entity=entity)


def test_only_the_open_connections_own_defaults_leave_the_scope():
    rows = [_row("assumed_connection_ramp", "tie"), _row("assumed_default", "point of connection"),
            _row("assumed_default", "hospital feeder losses"),      # another link: has flow
            _row("link_efficiency_out_of_range", "tie"),             # a warning, not a default
            _row("assumed_default", "tie", severity="error")]
    S._unused_connection_defaults(rows, frozenset({"tie"}))
    assert [r["in_scope"] for r in rows] == [False, False, True, True, True]


def test_a_grid_connection_brings_the_connection_defaults_back(client, tmp_projects_dir):
    """Keyed on the live section state, not the template: a user who says the
    site has a grid connection is asked to confirm its defaults again."""
    _microgrid_flow(client, "mg_grid")
    r = client.put(CONFIG_URL, json={"site_archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    conn, defaults = _connection_defaults(client.get("/api/projects/mg_grid/flow").json())
    assert conn["state"] != "not_needed"
    assert len(defaults) == 4 and all(i["in_scope"] for i in defaults)


@pytest.mark.parametrize("n, words", [(0, "0 assets"), (1, "1 asset"), (9, "9 assets")])
def test_a_summary_count_uses_a_real_plural(n, words):
    assert S._count(n, "asset") == words
    assert "(s)" not in words
