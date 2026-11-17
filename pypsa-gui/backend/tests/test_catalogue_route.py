"""
`GET /api/catalogue` and `GET /api/catalogue/guide/{id}` (spec expert-catalogue §3.1; GK B C1, C3).

The ETag is the sha256 of the exact response bytes for each query; a stale
If-None-Match gets 200 and the new body. A guide id is a key into the
catalogue, never a path: an unknown id, `../`, `%2e%2e` and an empty id all
get the same 404 body. The payload is project-free.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

from services.asset_schema import catalogue as C

ROUTE = "/api/catalogue"


def _tag(r) -> str:
    return r.headers["etag"]


def test_catalogue_body_and_etag(client):
    r = client.get(ROUTE)
    assert r.status_code == 200
    assert _tag(r) == '"' + hashlib.sha256(r.content).hexdigest() + '"'
    body = r.json()
    assert body["version"] == C.content_hash()
    assert len(body["entries"]) == len(C.entries())
    assert [g["id"] for g in body["groups"]][0] == "general"
    assert {"id", "plain_name", "model_name", "group", "depth", "functions", "default", "range",
            "options_plain", "aliases", "guide"} <= set(body["entries"][0])


def test_if_none_match_gives_304_and_a_stale_tag_gives_200(client):
    first = client.get(ROUTE)
    again = client.get(ROUTE, headers={"If-None-Match": _tag(first)})
    assert again.status_code == 304 and again.content == b""
    assert _tag(again) == _tag(first)
    stale = client.get(ROUTE, headers={"If-None-Match": '"0000"'})
    assert stale.status_code == 200 and stale.content == first.content


@pytest.mark.parametrize("query", [{"depth": "essentials"}, {"function": "load_flow"}])
def test_each_filter_changes_the_etag_and_narrows_entries(client, query):
    whole = client.get(ROUTE)
    part = client.get(ROUTE, params=query)
    assert part.status_code == 200
    assert _tag(part) != _tag(whole)
    assert _tag(part) == '"' + hashlib.sha256(part.content).hexdigest() + '"'
    # The whole catalogue's tag does not answer a filtered query.
    assert client.get(ROUTE, params=query, headers={"If-None-Match": _tag(whole)}).status_code == 200
    rows = part.json()["entries"]
    assert 0 < len(rows) < len(whole.json()["entries"])
    if "depth" in query:
        assert {e["depth"] for e in rows} == {"essentials"}
    else:
        assert all("load_flow" in e["functions"] for e in rows)


def test_depth_all_is_every_entry(client):
    assert len(client.get(ROUTE, params={"depth": "all"}).json()["entries"]) == len(C.entries())


@pytest.mark.parametrize("query", [{"depth": "everything"}, {"function": "lopf"}])
def test_unknown_filter_is_422(client, query):
    assert client.get(ROUTE, params=query).status_code == 422


def test_guide_for_a_known_id(client):
    eid = C.entries()[0].id
    r = client.get(f"{ROUTE}/guide/{eid}")
    assert r.status_code == 200
    assert r.json() == {"id": eid, "markdown": C.guide_body(eid)}
    assert client.get(f"{ROUTE}/guide/function.dispatch").status_code == 200


@pytest.mark.parametrize("raw", [
    "no.such_entry", "..%2Fcatalogue.py", "%2e%2e%2fcatalogue.ids.txt", "%2e%2e", "", "generator.capacity.md",
    "..%2F..%2Fmain.py", "/etc/passwd",
])
def test_guide_unknown_ids_share_one_404(client, raw):
    base = client.get(f"{ROUTE}/guide/no.such_entry")
    assert base.status_code == 404
    r = client.get(f"{ROUTE}/guide/{raw}")
    assert r.status_code == 404
    assert r.content == base.content


def test_guide_lookup_never_touches_the_filesystem(monkeypatch):
    """A crafted id reaches only the dict: no file outside guide/ is ever opened for it."""
    assert C.guide_body("../catalogue") is None
    assert C.guide_body("") is None


def test_catalogue_is_project_free_while_units_stay_per_asset(client, install_network):
    """Two projects that differ in bus units serve byte-identical catalogues (§3.1); the
    tonne-bus unit still comes per asset from the units route."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T
    from services.flow_service import _size_unit

    from tests.conftest import build_network

    install_network(build_network())
    on_energy = client.get(ROUTE)
    n = T.BUILDERS["eh_concentrator_staged"]()
    install_network(n)
    on_tonne = client.get(ROUTE)
    assert on_energy.content == on_tonne.content
    assert _tag(on_energy) == _tag(on_tonne)
    name = next((g for g in n.generators.index if _size_unit(n, n.generators.at[g, "bus"], stock=False) == "t/h"),
                None)
    if name is None:
        pytest.skip("template has no generator on a tonne bus")
    units = client.get(f"/api/network/asset_schema/Generator/units/{name}").json()
    assert json.dumps(units).count("t/h") >= 1
