"""
A zero CO2 result must never be silently caused by a missing emission factor.

A real project reported 0 tCO2 for a 300 MW gas plant. The number was correct:
its generator's carrier is `gas`, which was absent from the catalog, so
ensure_carrier created a bare row with co2_emissions=0.0 and the emissions
formula multiplied by it.

Nothing warned because both existing guards are CONDITIONAL — one on
co2_price > 0, the other on a global constraint existing. That network had
neither, which is why this warning is ungated.
"""
from __future__ import annotations

from services.carrier_catalog import CARRIER_CATALOG, ensure_carrier
from services.validation_service import _check_carrier_emissions, _looks_fossil


def _codes(client) -> list[str]:
    # NOTE: the brief's helper assumed `GET /api/validation`. The real
    # endpoint (verified against routers/simulation.py + frontend/src/api/
    # simulation.ts) is `POST /api/simulation/preflight`, returning
    # {"ok", "errors", "warnings", "issues": [Issue.to_dict(), ...]}. The
    # `issues[].code` shape the brief specified is unchanged — only the
    # path and HTTP method differ.
    r = client.post("/api/simulation/preflight")
    assert r.status_code == 200, r.text
    return [i["code"] for i in r.json().get("issues", [])]


def test_gas_is_in_the_catalog_with_the_same_factor_as_CCGT(client):
    # Justified by this repo's own entries rather than invented: CCGT and OCGT
    # burn natural gas and are already 0.187.
    assert CARRIER_CATALOG["gas"]["co2_emissions"] == CARRIER_CATALOG["CCGT"]["co2_emissions"]
    assert CARRIER_CATALOG["diesel"]["co2_emissions"] == CARRIER_CATALOG["oil"]["co2_emissions"]


def test_ensure_carrier_never_repairs_an_existing_row(client):
    # This is why the fix must be OFFERED rather than left to the catalog.
    import pypsa
    n = pypsa.Network()
    n.add("Carrier", "gas", nice_name="gas", color="", co2_emissions=0.0)
    ensure_carrier(n, "gas")
    assert float(n.carriers.at["gas", "co2_emissions"]) == 0.0


def test_looks_fossil_excludes_biogas():
    # `biogas` contains `gas` but its CO2 is biogenic and conventionally zero.
    # A false positive here teaches users to ignore the warning.
    assert _looks_fossil("gas") is True
    assert _looks_fossil("CCGT") is True
    assert _looks_fossil("lignite") is True
    assert _looks_fossil("diesel") is True
    assert _looks_fossil("biogas") is False
    assert _looks_fossil("solar") is False
    assert _looks_fossil("onwind") is False


def test_looks_fossil_excludes_synthetic_methanol_but_not_plain_ccgt_ocgt():
    # In this repo's PyPSA-Eur sector-coupled networks, `methanol` is
    # SYNTHETIC (methanolisation: H2 + captured CO2) — its carbon is
    # accounted for at capture, not at the burn. `CCGT methanol` / `OCGT
    # methanol` would otherwise trip on the `ccgt`/`ocgt` substrings despite
    # not being fossil gas plants. The exclusion must stay narrow: plain
    # `CCGT` / `OCGT` (actual fossil gas turbines) must NOT be disarmed.
    assert _looks_fossil("CCGT methanol") is False
    assert _looks_fossil("OCGT methanol") is False
    assert _looks_fossil("CCGT") is True
    assert _looks_fossil("OCGT") is True


def test_looks_fossil_matches_whole_words_not_bare_substrings():
    # 2026-07-31 review, Finding 2: bare substring matching (`x in c`)
    # produced demonstrated false positives that have nothing to do with the
    # biogas/methanol exclusions above — same defect class, never swept
    # against real carrier vocabulary until now.
    assert _looks_fossil("biomass boiler") is False                        # 'oil' in 'boiler'
    assert _looks_fossil("electric boiler") is False
    assert _looks_fossil("hydrogen boiler") is False
    assert _looks_fossil("residential rural biomass boiler") is False
    assert _looks_fossil("biomass gasification") is False                  # 'gas' in 'gasification'
    assert _looks_fossil("syngas") is False                                # ambiguous origin, see below
    assert _looks_fossil("charcoal") is False                              # 'coal' in 'charcoal'; biogenic
    assert _looks_fossil("waste heat") is False                            # byproduct stream, not a fuel

    # Word-boundary matching must not degrade into "never matches a real
    # fossil carrier" — every keyword must still fire on its own.
    assert _looks_fossil("gas") is True
    assert _looks_fossil("CCGT") is True
    assert _looks_fossil("coal") is True
    assert _looks_fossil("lignite") is True
    assert _looks_fossil("oil") is True
    assert _looks_fossil("diesel") is True

    # `syngas` alone is deliberately left unmatched (coal-derived syngas is
    # fossil, biomass-derived is not — the bare name doesn't say which, and
    # this module never invents an emission factor). A carrier that DOES
    # qualify its origin is still caught via that keyword's own match.
    assert _looks_fossil("coal syngas") is True
    assert _looks_fossil("biomass syngas") is False

    # `waste` alone (municipal solid waste as a combusted fuel) must keep
    # matching — only the "waste heat" byproduct-stream phrase is excluded.
    assert _looks_fossil("waste") is True


def test_curtailment_cost_check_does_not_treat_hydrogen_as_hydro():
    # Same "hydro ⊂ hydrogen" defect as isRenewableCarrier (frontend), folded
    # in here since this check mirrors that frontend logic.
    import pypsa

    from services.validation_service import _check_curtailment_cost

    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=380.0)
    n.add("Generator", "G1", bus="B1", carrier="hydrogen", p_nom=10.0)
    n.generators["curtailment_cost"] = 0.0
    n.generators.at["G1", "curtailment_cost"] = 5.0
    codes = [issue.code for issue in _check_curtailment_cost(n)]
    assert "curtailment_cost_on_thermal" in codes


def test_warns_when_carriers_table_is_completely_empty():
    # The API path always populates n.carriers via ensure_carrier, so it
    # cannot construct this state — build it directly, like
    # test_ensure_carrier_never_repairs_an_existing_row above.
    #
    # A network imported via n.import_from_netcdf / import_from_csv_folder
    # (routers/io.py) gets no ensure_carrier pass over its generators, so an
    # imported fossil-carrier generator with a completely empty Carrier
    # table (n.carriers.empty is True — no rows, though the schema columns
    # exist) is the LEAST-known-data case this warning exists for. The
    # per-carrier fallback (`c in n.carriers.index else 0.0`) already
    # degrades correctly for this; the check must not short-circuit before
    # reaching it.
    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=380.0)
    n.add("Generator", "G1", bus="B1", carrier="gas", p_nom=300.0)
    assert n.carriers.empty
    codes = [issue.code for issue in _check_carrier_emissions(n)]
    assert "carrier_zero_co2" in codes


def test_warns_for_a_fossil_carrier_with_no_intensity(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    # Zero the intensity the catalog would now supply, reproducing the state a
    # project created before this fix is in.
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.0})
    assert "carrier_zero_co2" in _codes(client)


def test_does_not_warn_for_renewables(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "solar", "p_nom": 300.0,
    })
    assert "carrier_zero_co2" not in _codes(client)


def test_does_not_warn_once_an_intensity_is_set(client):
    client.post("/api/network/buses", json={"name": "B1", "v_nom": 380.0, "x": 6.96, "y": 50.9})
    client.post("/api/network/generators", json={
        "name": "G1", "bus": "B1", "carrier": "gas", "p_nom": 300.0, "efficiency": 0.45,
    })
    client.put("/api/network/carriers/gas", json={"name": "gas", "co2_emissions": 0.187})
    assert "carrier_zero_co2" not in _codes(client)
