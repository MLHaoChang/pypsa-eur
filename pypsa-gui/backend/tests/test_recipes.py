"""Gap 03: a process's recipe as Guided section 2 shows it (services/recipes.py), and the
section 2 list that carries it (flow_service._holds)."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pypsa
import pytest

TPL_DIR = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
sys.path.insert(0, str(TPL_DIR))

import eh_ammonia as A  # noqa: E402
import eh_templates as T  # noqa: E402

from services import flow_service  # noqa: E402
from services.recipes import link_recipe, recipe_label, recipe_links  # noqa: E402


@pytest.fixture(scope="module")
def ammonia():
    return A.build_eh_ammonia_staged(T)


def test_haber_bosch_reads_the_templates_ledger(ammonia):
    r = link_recipe(ammonia, "haber_bosch")
    assert r["label"] == "Haber-Bosch"
    assert r["per"] == "t" and r["product_per"] == pytest.approx(A.MWH_PER_T)
    assert r["electricity"]["unit"] == "kWh"
    assert r["electricity"]["value"] == pytest.approx(A.EL_MWH_PER_T * 1000.0)
    [h2] = r["inputs"]
    assert (h2["port"], h2["carrier"], h2["unit"]) == (2, "H2", "MWh")
    assert h2["value"] == pytest.approx(A.H2_MWH_PER_T)


def test_an_electrolyser_counts_per_mwh_of_its_product():
    n = T.build_eh_h2_hub()
    r = link_recipe(n, "electrolyser")
    assert (r["label"], r["per"], r["inputs"]) == ("Electrolyser", "MWh", [])
    assert r["electricity"] == {"value": pytest.approx(1 / 0.68), "unit": "MWh"}


def test_the_built_year_names_a_staged_electrolyser(ammonia):
    assert recipe_label(ammonia, "electrolyser_new_2030") == "Electrolyser, built 2030"
    assert "electrolyser_new_2030" in recipe_links(ammonia)


def _process(**over):
    n = pypsa.Network()
    for b, c in (("el", "AC"), ("p", "NH3"), ("h", "H2")):
        n.add("Bus", b, carrier=c)
    n.add("Carrier", "H2", nice_name="Hydrogen")
    n.add(
        "Link",
        "unit",
        bus0="el",
        bus1="p",
        bus2="h",
        efficiency=2.0,
        efficiency2=-3.0,
        carrier="Synthesis",
    )
    n.links["eh_process"] = over.pop("flag", True)
    for k, v in over.items():
        n.links.at["unit", k] = v
    return n


def test_the_carriers_display_name_labels_an_input():
    r = link_recipe(_process(), "unit")
    assert r["inputs"][0]["label"] == "Hydrogen"
    assert r["product"]["label"] == "NH3" and r["per"] == "MWh"
    assert r["inputs"][0]["value"] == pytest.approx(1.5)
    assert r["electricity"]["value"] == pytest.approx(0.5)


@pytest.mark.parametrize("flag", ["True", 1.0, np.nan, False])
def test_only_an_exactly_true_process_flag_counts(flag):
    assert link_recipe(_process(flag=flag), "unit") is None


@pytest.mark.parametrize(
    "over",
    [
        {"efficiency": 0.0},
        {"efficiency": -1.0},
        {"efficiency": np.nan},
        {"efficiency2": 0.5},
    ],
)
def test_no_recipe_where_the_card_cannot_show_one(over):
    assert link_recipe(_process(**over), "unit") is None


def test_section_2_lists_the_carriers_then_the_recipes(ammonia):
    imports, connection = flow_service._connection(ammonia, {}, None)
    site = flow_service._site_assets(ammonia, imports, connection)
    carriers = [str(c) for c in ammonia.carriers.index]
    rows, totals = flow_service._holds(ammonia, carriers, site)
    held = rows["carriers"]
    assert [h["entity"] for h in held[: len(carriers)]] == carriers
    recipes = held[len(carriers) :]
    assert {h["entity"] for h in recipes} == set(recipe_links(ammonia))
    assert all(h["component"] == "Link" and h["recipe"]["label"] for h in recipes)
    assert totals["carriers"] == len(carriers) + len(recipes)
    # Section 5 keeps the same Links as assets, with their size and no recipe.
    assets = {h["entity"]: h for h in rows["assets"]}
    assert (
        "recipe" not in assets["haber_bosch"]
        and assets["haber_bosch"]["field"] == "p_nom"
    )
