"""The sizing loop's `commercial` refusal (GK A, #1711): the loop never applies a solve's price layer, so
it refuses a project whose `commercial` holds anything but the keys with no effect on the LP."""

from __future__ import annotations

import json
import pathlib

import pytest

from services.adequacy.sizing_loop_runner import COMMERCIAL_NO_LP_KEYS, refusals

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "project_templates"


class _Cfg:
    island_constraints = False
    fixed_sizes = "off"

    def __init__(self, commercial):
        self.commercial = commercial


def _lines(commercial) -> list[str]:
    return refusals(_Cfg(commercial))


def test_a_block_with_only_the_lp_free_keys_is_admitted():
    block = {
        "poc_link": "grid_import",
        "site_party": "site",
        "timezone": "UTC",
        "value_flows": {"asset_owners": []},
        "import_tariff": None,
        "contracts": [],
        "group_members": [],
        "export_link": None,
    }
    assert _lines(block) == []
    assert _lines(None) == [] and _lines({}) == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("import_tariff", {"items": [{"id": "network_energy"}]}),
        ("import_price_ref", {"id": "p"}),
        ("export_price_ref", {"id": "p"}),
        ("connection", {"id": "agreement"}),
        ("contracts", [{"id": "ppa"}]),
        ("group_contract", {"id": "g"}),
        ("group_cap_mw", 10.0),
        ("demand_items", [{"id": "d"}]),
        ("co2_price", {"id": "c"}),
        ("a_key_added_later", 1),
    ],
)
def test_any_other_truthy_key_refuses(key, value):
    lines = _lines({"poc_link": "grid_import", "value_flows": {"x": 1}, key: value})
    assert len(lines) == 1 and "commercial bindings" in lines[0]


def test_the_allowlist_is_exactly_the_keys_without_an_lp_effect():
    assert COMMERCIAL_NO_LP_KEYS == {
        "poc_link",
        "site_party",
        "timezone",
        "value_flows",
    }


def test_the_single_year_dc_template_is_admitted_and_ships_no_tariff():
    cfg = json.loads((TEMPLATES / "eh_datacenter" / "solver_config.json").read_text())
    assert cfg["commercial"]["import_tariff"] is None
    assert cfg["commercial"]["value_flows"]["tariff_payees"] == []
    assert _lines(cfg["commercial"]) == []


def test_the_staged_dc_template_keeps_its_tariff_and_is_refused():
    cfg = json.loads(
        (TEMPLATES / "eh_datacenter_staged" / "solver_config.json").read_text()
    )
    assert cfg["commercial"]["import_tariff"]
    assert len(_lines(cfg["commercial"])) == 1
