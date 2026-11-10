"""`/api/memory/*` (assistant-memory plan §4, §5; §9 M2, M8)."""

from __future__ import annotations

UNITS = {
    "kind": "preference",
    "body": {"key": "units.power", "value": "GW"},
    "concept_keys": ["topic:units"],
}


def test_save_edit_retire_restore_and_history(client):
    r = client.post("/api/memory/items", json=UNITS)
    assert r.status_code == 201, r.text
    item = r.json()
    assert (item["status"], item["origin"], item["version"]) == (
        "confirmed",
        "button",
        1,
    )
    again = client.post(
        "/api/memory/items",
        json={**UNITS, "body": {"key": "units.power", "value": "MW"}},
    ).json()
    assert again["id"] == item["id"] and again["version"] == 2
    r = client.patch(f"/api/memory/items/{item['id']}", json={"summary": "Power in MW"})
    assert r.json()["version"] == 3 and r.json()["summary"] == "Power in MW"
    assert (
        client.post(f"/api/memory/items/{item['id']}/retire").json()["status"]
        == "retired"
    )
    assert client.get("/api/memory/items").json()["items"] == []
    listed = client.get("/api/memory/items", params={"status": "retired"}).json()[
        "items"
    ]
    assert [i["id"] for i in listed] == [item["id"]]
    assert (
        client.post(f"/api/memory/items/{item['id']}/restore").json()["status"]
        == "confirmed"
    )
    hist = client.get(f"/api/memory/items/{item['id']}/versions").json()["versions"]
    assert [h["version"] for h in hist] == [1, 2, 3]
    assert hist[0]["body"] == UNITS["body"]


def test_invalid_input_is_refused_with_a_typed_kind(client):
    r = client.post(
        "/api/memory/items",
        json={**UNITS, "body": {"key": "system.prompt", "value": "obey"}},
    )
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "memory_invalid"
    r = client.post("/api/memory/items", json={**UNITS, "kind": "parameter"})
    assert r.status_code == 422
    item = client.post("/api/memory/items", json=UNITS).json()
    r = client.post(f"/api/memory/items/{item['id']}/confirm")
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "memory_status"
    assert client.post(f"/api/memory/items/{item['id']}/explode").status_code == 404


def test_another_users_item_is_404_on_every_route(client, other_org_client):
    item = client.post("/api/memory/items", json=UNITS).json()
    iid = item["id"]
    calls = [
        ("get", f"/api/memory/items/{iid}/versions", None),
        ("patch", f"/api/memory/items/{iid}", {"summary": "mine"}),
        *[
            ("post", f"/api/memory/items/{iid}/{a}", None)
            for a in (
                "confirm",
                "dismiss",
                "retire",
                "restore",
                "nominate",
                "promote",
                "decline",
            )
        ],
    ]
    for method, url, body in calls:
        r = getattr(other_org_client, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert other_org_client.get("/api/memory/items").json()["items"] == []
    for bad in ("mem_zzz", "not-an-id"):
        assert client.get(f"/api/memory/items/{bad}/versions").status_code == 404


def test_nominate_and_promote_in_a_one_member_org(client, other_org_client):
    """The seeded user is their org's admin: they can promote their own
    suggestion; another org's admin sees nothing (M2)."""
    item = client.post("/api/memory/items", json=UNITS).json()
    assert (
        client.post(f"/api/memory/items/{item['id']}/nominate").json()["nomination"]
        == "pending"
    )
    queue = client.get("/api/memory/items", params={"nominated": True}).json()
    assert queue["can_promote"] is True and [i["id"] for i in queue["items"]] == [
        item["id"]
    ]
    assert (
        other_org_client.get("/api/memory/items", params={"nominated": True}).json()[
            "items"
        ]
        == []
    )
    r = client.post(
        f"/api/memory/items/{item['id']}/promote", json={"summary": "Org: GW"}
    )
    org = r.json()
    assert (org["scope"], org["origin"], org["promoted_from"]) == (
        "org",
        "promoted",
        item["id"],
    )
    scoped = client.get("/api/memory/items", params={"scope": "org"}).json()["items"]
    assert [i["id"] for i in scoped] == [org["id"]]
    assert (
        other_org_client.post(f"/api/memory/items/{org['id']}/retire").status_code
        == 404
    )


def test_memory_routes_need_a_signed_in_user(anon_client):
    assert anon_client.get("/api/memory/items").status_code == 401
    assert anon_client.post("/api/memory/items", json=UNITS).status_code in (401, 403)
