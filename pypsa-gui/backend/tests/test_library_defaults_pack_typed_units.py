"""
Typed cost units in the defaults pack (GS-2b; owner decision R-1021 item 1).

Every row of a typed version states `capacity_basis` (what its capacity measures),
`includes_grid_connection` and `money_year`; `price_from_pack` scales by the ratio from the
component's nominal to the row's basis, not by the component type alone. An electrolysis Link
(a price per kW of electrical INPUT) used to come out x0.62 too cheap.

The scaling rule is one table, `pricing.SCALING_RULES` (component x capacity basis -> scale or
refusal); `test_scaling_table_cell` covers every cell.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pypsa
import pytest

from services.library.defaults_pack import loader
from services.library.defaults_pack.loader import (
    CAPACITY_BASES, LEGACY_VERSIONS, DefaultsPackError, LegacyPackVersion, load_defaults_pack)

TYPED = "2026-10-22"
FIXTURES = Path(__file__).parent / "fixtures" / "defaults_pack"


def _v(key, pack=None):
    return next(r.value for r in (pack or load_defaults_pack(TYPED)).cost_values if r.key == key)


def _net():
    n = pypsa.Network()
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3")
    n.add("Generator", "g", bus="ac", p_nom_extendable=True)
    n.add("StorageUnit", "s", bus="ac", p_nom_extendable=True, max_hours=4.0)
    n.add("Store", "tank", bus="nh3", carrier="NH3", e_nom_extendable=True)
    n.add("Link", "elz", bus0="ac", bus1="h2", p_nom_extendable=True, efficiency=0.62)
    n.add("Link", "fc", bus0="h2", bus1="ac", p_nom_extendable=True, efficiency=0.5)
    n.add("Link", "hb", bus0="ac", bus1="nh3", p_nom_extendable=True, efficiency=0.8)
    return n


# ── the three tests the gatekeeper asked for first ──────────────────────────


def test_electrolysis_link_overnight_equals_the_row():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    out = price_from_pack(n, "links", "elz", "electrolysis", version=TYPED)
    assert n.links.at["elz", "overnight_cost"] == pytest.approx(_v("electrolysis.investment.overnight"))
    assert out["scale"] == 1.0 and out["capacity_basis"] == "input_electric"


def test_a_product_priced_link_scales_by_the_product_bus_efficiency():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    out = price_from_pack(n, "links", "hb", "haber-bosch", version=TYPED)
    assert n.links.at["hb", "overnight_cost"] == pytest.approx(
        0.8 * _v("haber-bosch.investment.overnight"))
    assert out["scale"] == 0.8 and out["capacity_basis"] == "output_product"


def test_regional_battery_names_its_currency(monkeypatch):
    """The battery branch returned a region's numbers under an `_eur_` key. No shipped regional
    pack carries battery rows yet, so give the uk pack the EU battery rows (as if it did)."""
    from services.library.defaults_pack import pricing

    uk, eu = load_defaults_pack(TYPED, region="uk"), load_defaults_pack(TYPED)
    rows = [r for r in eu.cost_values if r.technology == "battery"]
    pack = uk.model_copy(update={"cost_values": [*uk.cost_values, *rows]})
    monkeypatch.setattr(pricing, "load_defaults_pack", lambda *a, **k: pack)
    out = pricing.price_from_pack(_net(), "storage_units", "s", "battery", version=TYPED, region="uk")
    assert out["currency"] == "GBP" and "annualised_cost_eur_per_mw_yr" not in out
    assert out["annualised_cost_per_mw_yr"] == pytest.approx(out["capital_cost"] + out["fom_cost"])


def test_a_regional_battery_is_refused_not_priced_in_the_wrong_currency():
    from services.library.defaults_pack.loader import MissingRegionalRow
    from services.library.defaults_pack.pricing import price_from_pack

    with pytest.raises(MissingRegionalRow):
        price_from_pack(_net(), "storage_units", "s", "battery", version=TYPED, region="uk")


def test_eu_battery_returns_its_currency_and_the_eur_key():
    from services.library.defaults_pack.pricing import price_from_pack

    out = price_from_pack(_net(), "storage_units", "s", "battery", version=TYPED)
    assert out["currency"] == "EUR" and out["annualised_cost_eur_per_mw_yr"] > 0
    assert out["money_year"] == 2020 and out["capacity_basis"] == {
        "power": "output_electric_ac", "energy": "storage_energy"}


# ── the fuel cell and the stamp ─────────────────────────────────────────────


def test_fuel_cell_link_still_scales_by_efficiency():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    out = price_from_pack(n, "links", "fc", "fuel-cell", version=TYPED)
    assert n.links.at["fc", "overnight_cost"] == pytest.approx(0.5 * _v("fuel-cell.investment.overnight"))
    assert out["scale"] == 0.5 and out["capacity_basis"] == "output_electric"


def test_the_stamp_carries_currency_money_year_and_grid_connection():
    from services.library.defaults_pack.pricing import price_from_pack

    out = price_from_pack(_net(), "generators", "g", "onwind", version=TYPED)
    assert (out["currency"], out["money_year"]) == ("EUR", 2020)
    assert out["includes_grid_connection"] == "unknown" and out["connection_fee_review"] is True
    assert "annualised_cost_eur_per_mw_yr" in out


def test_a_regional_stamp_carries_the_regions_currency():
    from services.library.defaults_pack.pricing import price_from_pack

    out = price_from_pack(_net(), "generators", "g", "onwind", version=TYPED, region="uk")
    assert (out["currency"], out["money_year"]) == ("GBP", 2024)
    assert out["includes_grid_connection"] is False and out["connection_fee_review"] is False
    assert "annualised_cost_per_mw_yr" in out and "annualised_cost_eur_per_mw_yr" not in out


def test_a_row_that_includes_the_connection_is_flagged_in_the_provenance():
    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "generators", "g", "onwind", version=TYPED, region="us_federal")
    rec = provenance.get(n, "Generator", "g")["overnight_cost"]
    assert rec["includes_grid_connection"] == "unknown" and rec["capacity_basis"] == "output_electric"
    assert rec["connection_fee_review"] is True


# ── the scaling table: one test per cell ────────────────────────────────────

_PRODUCT_SELECT = {"nh3": "haber-bosch"}
_CELLS = [
    # (component, name, row technology, expected scale or None for a refusal)
    ("generators", "g", "onwind", 1.0),                       # Generator x output_electric
    ("generators", "g", "solar-utility", 1.0),                # Generator x output_electric_ac
    ("generators", "g", "electrolysis", None),                # Generator x input_electric
    ("generators", "g", "haber-bosch", None),                 # product row, bus is electric
    ("generators", "g", "nh3-tank", None),                    # Generator x storage_energy
    ("storage_units", "s", "onwind", 1.0),                    # StorageUnit x output_electric
    ("storage_units", "s", "electrolysis", None),
    ("storage_units", "s", "haber-bosch", None),
    ("storage_units", "s", "nh3-tank", None),                 # the battery prices energy by parts
    ("stores", "tank", "nh3-tank", 1.0),                      # Store x storage_energy
    ("stores", "tank", "onwind", None),
    ("stores", "tank", "electrolysis", None),
    ("stores", "tank", "haber-bosch", None),
    ("links", "fc", "fuel-cell", 0.5),                        # Link x output_electric: eta, electric out
    ("links", "fc", "solar-utility", 0.5),                    # Link x output_electric_ac
    ("links", "elz", "electrolysis", 1.0),                    # Link x input_electric: bus0 electric
    ("links", "fc", "electrolysis", None),                    # ... bus0 is H2: refused
    ("links", "hb", "haber-bosch", 0.8),                      # Link x output_product: product bus eta
    ("links", "fc", "haber-bosch", None),                     # no NH3 bus on the Link
    ("links", "hb", "onwind", 0.8),                           # electric output absent -> see below
    ("links", "elz", "nh3-tank", None),                       # Link x storage_energy
]


@pytest.mark.parametrize("component,name,technology,expected", [
    c for c in _CELLS if not (c[0] == "links" and c[1] == "hb" and c[2] == "onwind")])
def test_scaling_table_cell(component, name, technology, expected):
    from services.library.defaults_pack.pricing import CapacityBasisMismatch, price_from_pack

    n = _net()
    if expected is None:
        before = getattr(n, component).copy()
        with pytest.raises(CapacityBasisMismatch):
            price_from_pack(n, component, name, technology, version=TYPED)
        assert getattr(n, component).equals(before), "a refusal writes nothing"
        return
    out = price_from_pack(n, component, name, technology, version=TYPED)
    assert out["scale"] == pytest.approx(expected)
    assert getattr(n, component).at[name, "overnight_cost"] == pytest.approx(
        expected * _v(f"{technology}.investment.overnight"))


def test_a_link_whose_electric_output_is_not_on_a_bus_is_refused():
    from services.library.defaults_pack.pricing import CapacityBasisMismatch, price_from_pack

    with pytest.raises(CapacityBasisMismatch):                # hb: outputs NH3, no electric bus
        price_from_pack(_net(), "links", "hb", "onwind", version=TYPED)


def test_a_not_applicable_row_is_never_priced_per_capacity():
    from services.library.defaults_pack.pricing import CapacityBasisMismatch, price_from_pack

    with pytest.raises(CapacityBasisMismatch):
        price_from_pack(_net(), "generators", "g", "transformer.tr_132_33_40", version=TYPED)


# ── the legacy versions ─────────────────────────────────────────────────────


def test_legacy_versions_load_for_provenance_but_cannot_price():
    from services.library.defaults_pack.pricing import price_from_pack

    for v in ("2026-10-13", "2026-10-20"):
        pack = load_defaults_pack(v)
        assert pack.typed is False and all(r.capacity_basis is None for r in pack.cost_values)
        with pytest.raises(LegacyPackVersion) as err:
            price_from_pack(_net(), "generators", "g", "onwind", version=v)
        assert TYPED in str(err.value) and err.value.successor == TYPED


def test_a_legacy_regional_version_is_refused_and_names_its_successor():
    from services.library.defaults_pack.pricing import price_from_pack

    with pytest.raises(LegacyPackVersion) as err:
        price_from_pack(_net(), "generators", "g", "onwind", version="2026-10-21", region="uk")
    assert err.value.successor == TYPED


def test_the_legacy_set_is_pinned():
    shipped_untyped = {p.version for v in loader.available_versions()
                       if not (p := load_defaults_pack(v)).typed}
    regional = {f"{p.version}" for r in loader.available_regions()
                for d in (m["date"] for m in loader._region_dirs() if m["region"] == r)
                if not (p := load_defaults_pack(d, region=r)).typed}
    assert shipped_untyped == {"2026-10-05", "2026-10-07", "2026-10-13", "2026-10-20"}
    assert regional == {"2026-10-21+ca_federal", "2026-10-21+uk", "2026-10-21+us_federal"}
    assert LEGACY_VERSIONS == shipped_untyped | regional, "a new version must be typed"


def test_the_latest_versions_are_typed():
    assert load_defaults_pack().version == TYPED and load_defaults_pack().typed
    for r in loader.available_regions():
        assert load_defaults_pack(region=r).typed


# ── the schema: every shipped version and regional pack ─────────────────────


def _typed_dirs():
    for p in sorted(loader.VERSIONS_DIR.iterdir()):
        if p.is_dir() and p.name not in LEGACY_VERSIONS:
            yield p


@pytest.mark.parametrize("directory", list(_typed_dirs()), ids=lambda p: p.name)
def test_every_typed_row_states_its_fields(directory):
    rows = list(csv.DictReader((directory / "values.csv").open(newline="")))
    assert rows and "currency_year" not in rows[0] and "money_year" in rows[0]
    for r in rows:
        assert r["capacity_basis"] in CAPACITY_BASES, r["key"]
        assert r["includes_grid_connection"] in ("true", "false", "unknown"), r["key"]
        if r["currency"]:
            assert r["money_year"], r["key"]


def _copy_with(tmp_path, monkeypatch, mutate, name=TYPED):
    dst = tmp_path / "versions"
    shutil.copytree(loader.VERSIONS_DIR, dst)
    path = dst / name / "values.csv"
    rows = list(csv.reader(path.open(newline="")))
    mutate(rows)
    csv.writer(path.open("w", newline="")).writerows(rows)
    monkeypatch.setattr(loader, "VERSIONS_DIR", dst)
    loader._cached.cache_clear()
    loader._cached_region.cache_clear()
    return dst


@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    loader._cached.cache_clear()
    loader._cached_region.cache_clear()


def _blank(column):
    def mutate(rows):
        i = rows[0].index(column)
        rows[1][i] = ""
    return mutate


@pytest.mark.parametrize("column", ["capacity_basis", "includes_grid_connection"])
def test_a_typed_row_without_a_typed_field_is_refused(tmp_path, monkeypatch, column):
    _copy_with(tmp_path, monkeypatch, _blank(column))
    with pytest.raises(DefaultsPackError, match=column):
        load_defaults_pack(TYPED)


def test_a_money_row_without_money_year_is_refused(tmp_path, monkeypatch):
    def mutate(rows):
        h = rows[0]
        r = next(r for r in rows[1:] if r[h.index("currency")])
        r[h.index("money_year")] = ""
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="money"):
        load_defaults_pack(TYPED)


def test_an_unknown_capacity_basis_is_refused(tmp_path, monkeypatch):
    def mutate(rows):
        rows[1][rows[0].index("capacity_basis")] = "output_thermal"
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="capacity_basis"):
        load_defaults_pack(TYPED)


def test_a_grid_connection_is_true_false_or_unknown_never_null(tmp_path, monkeypatch):
    def mutate(rows):
        rows[1][rows[0].index("includes_grid_connection")] = "maybe"
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="includes_grid_connection"):
        load_defaults_pack(TYPED)


def test_a_typed_file_carrying_the_legacy_year_column_is_refused(tmp_path, monkeypatch):
    def mutate(rows):
        rows[0][rows[0].index("money_year")] = "currency_year"
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="header"):
        load_defaults_pack(TYPED)


def test_a_typed_file_carrying_both_year_columns_is_refused(tmp_path, monkeypatch):
    def mutate(rows):
        rows[0].append("currency_year")
        for r in rows[1:]:
            r.append("")
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="header"):
        load_defaults_pack(TYPED)


def test_rows_of_one_technology_part_share_a_capacity_basis(tmp_path, monkeypatch):
    def mutate(rows):
        h = rows[0]
        r = next(r for r in rows[1:] if r[h.index("key")] == "onwind.investment.lifetime")
        r[h.index("capacity_basis")] = "output_electric_dc"
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="onwind"):
        load_defaults_pack(TYPED)


def test_a_capacity_basis_must_fit_the_rows_unit_basis(tmp_path, monkeypatch):
    def mutate(rows):
        h = rows[0]
        for r in rows[1:]:
            if r[h.index("technology")] == "nh3-tank":
                r[h.index("capacity_basis")] = "output_electric"
    _copy_with(tmp_path, monkeypatch, mutate)
    with pytest.raises(DefaultsPackError, match="capacity_basis"):
        load_defaults_pack(TYPED)


def test_a_regional_row_must_agree_with_the_eu_rows_capacity_basis(tmp_path, monkeypatch):
    def mutate(rows):
        h = rows[0]
        for r in rows[1:]:
            if r[h.index("key")].startswith("onwind.investment"):
                r[h.index("capacity_basis")] = "output_electric_ac"
    dst = tmp_path / "versions"
    shutil.copytree(loader.VERSIONS_DIR, dst)
    path = dst / f"{TYPED}+uk" / "values.csv"
    rows = list(csv.reader(path.open(newline="")))
    mutate(rows)
    csv.writer(path.open("w", newline="")).writerows(rows)
    monkeypatch.setattr(loader, "VERSIONS_DIR", dst)
    loader._cached.cache_clear()
    loader._cached_region.cache_clear()
    with pytest.raises(DefaultsPackError, match="capacity basis"):
        load_defaults_pack(TYPED, region="uk")


# ── agreed values ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("tech,basis", [
    ("onwind", "output_electric"), ("solar-utility", "output_electric_ac"),
    ("solar-rooftop", "output_electric_ac"), ("electrolysis", "input_electric"),
    ("fuel-cell", "output_electric"), ("haber-bosch", "output_product"),
    ("nh3-tank", "storage_energy"), ("h2-tank", "storage_energy")])
def test_eu_rows_state_what_their_capacity_measures(tech, basis):
    pack = load_defaults_pack(TYPED)
    assert {r.capacity_basis for r in pack.cost_values if r.technology == tech} == {basis}


@pytest.mark.parametrize("region,grid", [("ca_federal", False), ("uk", False),
                                         ("us_federal", "unknown")])
def test_regional_rows_state_the_agreed_grid_connection_and_basis(region, grid):
    pack = load_defaults_pack(TYPED, region=region)
    over = {r.technology: r for r in pack.cost_values if r.parameter == "overnight"}
    assert over["solar-utility"].capacity_basis == "output_electric_ac"
    assert over["onwind"].capacity_basis == over["gas-genset"].capacity_basis == "output_electric"
    assert all(r.includes_grid_connection == grid for r in over.values())


def test_the_new_versions_keep_every_legacy_number():
    old, new = load_defaults_pack("2026-10-20"), load_defaults_pack(TYPED)
    assert [(r.key, r.value, r.unit, r.currency, r.currency_year) for r in old.cost_values] == \
           [(r.key, r.value, r.unit, r.currency, r.money_year) for r in new.cost_values]
    assert old.finance == new.finance and old.tariffs == new.tariffs
    for region in loader.available_regions():
        o = load_defaults_pack("2026-10-21", region=region)
        w = load_defaults_pack(TYPED, region=region)
        assert [(r.key, r.value, r.currency, r.currency_year) for r in o.cost_values] == \
               [(r.key, r.value, r.currency, r.money_year) for r in w.cost_values]


def test_capacity_basis_vocabulary_matches_gs_eus():
    from services.library.europe_reference import CapacityBasisCode

    assert set(CAPACITY_BASES) == set(CapacityBasisCode.__args__)


def test_the_new_hashes_are_pinned():
    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    regional = json.loads((FIXTURES / "regional_pack_hashes.json").read_text())["generic_defaults_regional"]
    assert pins[TYPED] == load_defaults_pack(TYPED).hash
    for region in loader.available_regions():
        assert regional[f"{TYPED}+{region}"] == load_defaults_pack(TYPED, region=region).hash


# ── the double-counted connection ───────────────────────────────────────────


def _priced_net(flag):
    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "generators", "g", "onwind", version=TYPED)
    provenance.annotate(n, "Generator", "g", "overnight_cost", includes_grid_connection=flag)
    return n


def test_a_row_including_the_connection_warns_when_a_connection_fee_is_charged():
    from services.commercial.preflight import _connection_double_count

    out = _connection_double_count(_priced_net(True), object())
    assert [(o[0], o[1], o[2], o[3]) for o in out] == [
        ("warning", "commercial.grid_connection_double_count", "Generator", "g")]


def test_no_connection_fee_or_a_row_that_leaves_it_out_does_not_warn():
    from services.commercial.preflight import _connection_double_count

    assert _connection_double_count(_priced_net(True), None) == []
    assert _connection_double_count(_priced_net(False), object()) == []
    assert _connection_double_count(_priced_net("unknown"), object()) == []


# ── seeds and example templates keep their numbers, per asset ───────────────

_ASSET_COLUMNS = ["overnight_cost", "lifetime", "fom_cost", "discount_rate", "capital_cost"]
#: The stated change (GS-2b, accepted by the engine gatekeeper): the staged hubs priced their
#: electrolysis Link x its efficiency (0.68); the row is per kW of electrical input.
_ELECTROLYSER_ETA = 0.68
_ELECTROLYSER_ASSETS = {f"Link:electrolyser_new_{y}" for y in (2027, 2030, 2035)}


def _dump_priced(n):
    out = {}
    for comp, cls in (("generators", "Generator"), ("storage_units", "StorageUnit"),
                      ("links", "Link"), ("stores", "Store")):
        df = getattr(n, comp)
        for name in df.index:
            row = {c: (None if (v := df.at[name, c]) != v else round(float(v), 9))
                   for c in _ASSET_COLUMNS if c in df.columns}
            if row.get("overnight_cost") is not None or row.get("capital_cost") not in (None, 0.0):
                out[f"{cls}:{name}"] = row
    return out


def test_every_template_and_redundancy_asset_keeps_its_price_but_the_electrolysers():
    import project_templates.eh_templates as T
    from services.adequacy.redundancy import apply_redundancy_scenario

    before = json.loads((FIXTURES / "template_prices_before_typed_units.json").read_text())
    now = {tid: _dump_priced(T.BUILDERS[tid]())
           for tid in sorted(k for k in T.BUILDERS if k.startswith("eh_"))}
    for sid in ("n1_generation", "n1_conversion", "parallel_storage@2"):
        n = T.BUILDERS["eh_datacenter"]()
        apply_redundancy_scenario(n, sid)
        now[f"redundancy:{sid}"] = _dump_priced(n)
    assert now.keys() == before.keys()
    changed = []
    for template, assets in before.items():
        assert now[template].keys() == assets.keys(), template
        for asset, cols in assets.items():
            if now[template][asset] == cols:
                continue
            changed.append((template, asset))
            assert asset in _ELECTROLYSER_ASSETS, f"{template} {asset} changed"
            for col in ("overnight_cost", "fom_cost"):
                assert now[template][asset][col] == pytest.approx(cols[col] / _ELECTROLYSER_ETA, rel=1e-9)
            for col in ("lifetime", "discount_rate", "capital_cost"):
                assert now[template][asset][col] == cols[col]
    assert {t for t, _ in changed} == {"eh_h2_hub_staged", "eh_ammonia_staged"}
    assert len(changed) == 6


def test_a_project_made_from_an_old_pin_still_resolves_its_pack():
    """The templates repinned to the typed version; a saved project that names an old version (a
    tariff stamp, a provenance source `generic_defaults@2026-10-13#<hash16>`) still resolves it."""
    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    for version in ("2026-10-13", "2026-10-20"):
        pack = load_defaults_pack(version)
        assert pack.hash == pins[version]
        assert loader.parse_pack_stamp(pack.stamp) == (pack.pack_id, version, pack.hash)
        tid = next(iter(pack.tariffs))
        assert pack.tariff_is_unchanged(pack.pack_tariff(tid))


def test_opening_a_saved_project_never_reprices_it_from_the_pack(client, api_project, monkeypatch):
    """Saved projects keep the costs written on their networks: nothing reads the pack or prices an
    asset while a project is activated, evicted and reloaded from disk (GS-2b gatekeeper, d)."""
    from services.library.defaults_pack import pricing
    from services.pypsa_service import PyPSAService

    calls = []

    def boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("a pack read while opening a project")

    name = api_project("alpha")
    r = client.post("/api/network/generators",
                    json={"name": "G1", "bus": "B1", "p_nom": 5.0, "capital_cost": 123.0})
    assert r.status_code in (200, 201), r.text
    monkeypatch.setattr(pricing, "price_from_pack", boom)
    monkeypatch.setattr(pricing, "load_defaults_pack", boom)
    monkeypatch.setattr(loader, "load_defaults_pack", boom)
    assert client.post(f"/api/projects/{name}/activate").status_code == 200
    assert client.post(f"/api/projects/{name}", params={"expect": name}).status_code == 200
    monkeypatch.setattr(PyPSAService, "RESIDENT_CAP", 0)
    PyPSAService._evict_if_over_cap()
    assert client.get(f"/api/projects/{name}").status_code == 200        # reloaded from disk
    gens = client.get("/api/network/generators").json()
    assert [g["capital_cost"] for g in gens if g["name"] == "G1"] == [123.0]
    assert calls == []
