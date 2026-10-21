"""B9: a section-5 "to check" item when asset costs mix money years.

A Library cost reference is applied as-is in its own money year (no index is
declared), so a project can hold 2020 EUR and 2025 EUR costs side by side. The
flow says so once, in Assets & sizing, as an acknowledgeable warning bound to
the set of years: it re-raises when a year is added or removed, not when an
asset joins a year already listed. It never blocks Run.
"""
from __future__ import annotations

from tests.test_project_flow import _flow, _site_network, edit, site  # noqa: F401 — fixtures

CODE = "asset_cost_money_years_mixed"


def _cost_year(n, klass: str, name: str, year: int) -> None:
    meta = n.meta if isinstance(n.meta, dict) else {}
    params = meta.setdefault("asset_params", {})
    params.setdefault(klass, {}).setdefault(name, {})["overnight_cost"] = {
        "value": 1.0, "currency_year": year}
    n.meta = meta


def _items(body) -> list[dict]:
    return [i for s in body["sections"] for i in s["items"] if i["code"] == CODE]


def _net():
    n = _site_network()
    n.add("Generator", "pv", bus="site", carrier="gas", p_nom=5.0)
    n.add("Generator", "wind", bus="site", carrier="gas", p_nom=5.0)
    return n


def test_one_year_or_none_recorded_says_nothing(client, site, edit):  # noqa: F811
    name = site(_net())
    assert _items(_flow(client, name)) == []
    with edit() as n:
        _cost_year(n, "Generator", "gas", 2020)
        _cost_year(n, "Generator", "pv", 2020)
    assert _items(_flow(client, name)) == []


def test_two_years_is_one_warning_in_assets_and_never_blocks(client, site, edit):  # noqa: F811
    name = site(_net())
    ready = _flow(client, name)["run_ready"]
    with edit() as n:
        _cost_year(n, "Generator", "gas", 2020)
        _cost_year(n, "Generator", "pv", 2025)
    body = _flow(client, name)
    [item] = _items(body)
    assert item["severity"] == "warning" and item["blocking"] is False
    assert item["target"]["section"] == "assets"
    assert item["target"]["component"] == "Generator"
    assert item["target"]["entity"] == "pv"                 # the newest year's first asset
    assert item["target"]["field"] == "capital_cost"
    assert item["message"] == ("Asset costs use different money years (2020, 2025), "
                               "not adjusted for inflation.")
    assert body["run_ready"] is ready


def test_the_ack_holds_for_the_same_years_and_re_raises_on_a_new_one(client, site, edit):  # noqa: F811
    name = site(_net())
    with edit() as n:
        _cost_year(n, "Generator", "gas", 2020)
        _cost_year(n, "Generator", "pv", 2025)
    [item] = _items(_flow(client, name))
    r = client.post(f"/api/projects/{name}/flow/ack",
                    json={"code": CODE, "entity": item["target"]["entity"]})
    assert r.status_code == 200, r.text
    assert _items(_flow(client, name))[0]["acknowledged"] is True
    # Another asset joins 2020: the set of years is the same, the ack holds.
    with edit() as n:
        _cost_year(n, "Generator", "wind", 2020)
    assert _items(_flow(client, name))[0]["acknowledged"] is True
    # A third year: re-raised.
    with edit() as n:
        _cost_year(n, "Generator", "wind", 2023)
    [again] = _items(_flow(client, name))
    assert again["acknowledged"] is False
    assert "2020, 2023, 2025" in again["message"]


def test_it_clears_when_one_year_remains(client, site, edit):  # noqa: F811
    name = site(_net())
    with edit() as n:
        _cost_year(n, "Generator", "gas", 2020)
        _cost_year(n, "Generator", "pv", 2025)
    assert len(_items(_flow(client, name))) == 1
    with edit() as n:
        _cost_year(n, "Generator", "pv", 2020)
    assert _items(_flow(client, name)) == []
