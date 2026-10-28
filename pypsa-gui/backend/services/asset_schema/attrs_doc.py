"""
The chat tool's attribute docs (S1d-3): `create_component` documents the `attrs` it takes from the same
descriptors the Expert card and the creation form render (`fields.py`), so the assistant is told the stored
units, the investment parts and which columns are derived, and the three faces cannot drift apart.

The chat writes the stored columns directly (no display scaling), so every unit here is `unit_storage`: money per
MW, MWh or MVA, shares and efficiencies as fractions. One line per class, compact: the text rides in the tool
catalogue on every turn.

The catalogue imports the generated literal `ATTRS_DOC`, not `attrs_doc()`: the assistant inventory reads the
catalogue's imports as literals and never imports application code (smoke/assistant_inventory.py). A test keeps
the literal equal to `attrs_doc()`; after a descriptor change, regenerate it from pypsa-gui/backend with
`python -m services.asset_schema.attrs_doc --write`.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from services.asset_schema.fields import FieldSpec, fields_for, known_components
from services.asset_schema.schema import composite_parts

_RULES = (
    "Attributes by class (other PyPSA attributes work too; unlisted classes take PyPSA's own). Stored "
    "units: money per MW, MWh or MVA (not kW); shares and efficiencies as fractions (0.4, not 40). * = "
    "required. Price assets by overnight cost and lifetime (or parts); omit derived columns."
)


def _entry(f: FieldSpec) -> str:
    star = "*" if f.required else ""
    if f.kind == "enum" and f.options:
        return f"{f.key}{star}={'|'.join(f.options)}"
    if f.kind == "bool":
        return f"{f.key}{star} (bool)"
    return f"{f.key}{star} ({f.unit_storage})" if f.unit_storage else f"{f.key}{star}"


def _parts_note(component: str) -> str:
    notes = []
    for p in composite_parts(component):
        scale = f", × {p.scale_by}" if p.scale_by else ""
        life = "" if p.own_lifetime else f"; uses the {component}'s lifetime, which must then be finite"
        notes.append(f"{p.prefix}_* is the {p.name} part ({p.stored_unit}{scale}{life})")
    return f" Investment parts: {'; '.join(notes)}." if notes else ""


def class_line(component: str) -> str:
    """One class's attributes: typed ones in the card's order, then the derived ones."""
    specs = [f for f in fields_for(component) if f.key != "name"]
    # Comma without a space: this text rides in the tool catalogue on every turn, and the 4000-character cap
    # (`test_the_docs_stay_compact`) is tight.
    typed = ",".join(_entry(f) for f in specs if not f.derived)
    derived = [f.key for f in specs if f.derived]
    tail = f"; derived: {','.join(derived)}" if derived else ""
    return f"{component}: {typed}{tail}.{_parts_note(component)}"


def attrs_doc() -> str:
    """The `attrs` description of `create_component`: the rules, then one line per described class."""
    return "\n".join((_RULES, *(class_line(c) for c in known_components())))


_BEGIN = "# BEGIN GENERATED (python -m services.asset_schema.attrs_doc --write)\n"
_END = "# END GENERATED\n"


def _literal(text: str, width: int = 100) -> str:
    chunks = []
    for line in text.split("\n"):
        pieces = textwrap.wrap(line, width, drop_whitespace=False, break_long_words=False,
                               break_on_hyphens=False) or [""]
        pieces[-1] += "\n"
        chunks.extend(pieces)
    chunks[-1] = chunks[-1][:-1]
    body = "".join(f"    {json.dumps(c, ensure_ascii=False)}\n" for c in chunks)
    return f"ATTRS_DOC = (\n{body})\n"


def _write() -> None:
    path = Path(__file__)
    src = path.read_text()
    head, rest = src.split(_BEGIN, 1)
    _, tail = rest.split(_END, 1)
    path.write_text(head + _BEGIN + _literal(attrs_doc()) + _END + tail)


# BEGIN GENERATED (python -m services.asset_schema.attrs_doc --write)
ATTRS_DOC = (
    "Attributes by class (other PyPSA attributes work too; unlisted classes take PyPSA's own). Stored "
    "units: money per MW, MWh or MVA (not kW); shares and efficiencies as fractions (0.4, not 40). * = "
    "required. Price assets by overnight cost and lifetime (or parts); omit derived columns.\n"
    "Bus: carrier,control=PQ|PV|Slack,country,unit,mwh_per_t (MWh/t),product_value,sub_network,x (°E),y "
    "(°N),v_nom (kV).\n"
    "Carrier: nice_name,color,unit,co2_emissions (t/MWh).\n"
    "Generator: carrier,bus*,p_nom (MW),p_nom_extendable (bool),p_nom_min (MW),p_nom_max "
    "(MW),overnight_cost (EUR/MW),lifetime (years),build_year (year),discount_rate (fraction),fom_cost "
    "(EUR/MW/yr),marginal_cost (EUR/MWh),curtailment_cost (EUR/MWh),start_up_cost (EUR),shut_down_cost "
    "(EUR),efficiency (fraction),p_min_pu (fraction),p_max_pu (fraction),committable (bool),ramp_limit_up"
    " (fraction),ramp_limit_down (fraction),min_up_time (h),min_down_time (h),e_sum_min (MWh),e_sum_max "
    "(MWh),control=PQ|PV|Slack,outage_rate_value (fraction),outage_rate_basis=FOR|EFORd,mttr_hours "
    "(h),p_max_pu_includes_outages (bool); derived: capital_cost.\n"
    "GlobalConstraint: "
    "type=primary_energy|tech_capacity_expansion_limit|operational_limit|transmission_volume_expansion_limit|transmission_expansion_cost_limit,sense=<=|==|>=,constant*,carrier_attribute,carrier,investment_period.\n"
    "Line: carrier,bus0*,bus1*,s_nom (MVA),s_nom_extendable (bool),s_nom_min (MVA),s_nom_max (MVA),length"
    " (km),inv_per_km_overnight (EUR/MVA/km),lifetime (years),build_year (year),discount_rate "
    "(fraction),inv_per_km_fom_share (fraction),outage_rate_value "
    "(fraction),outage_rate_basis=FOR|EFORd,mttr_hours (h); derived: capital_cost,fom_cost. Investment "
    "parts: inv_per_km_* is the per_km part (EUR/MVA/km, × length; uses the Line's lifetime, which must "
    "then be finite).\n"
    "Link: carrier,bus0*,bus1*,bus2,bus3,bus4,eh_process "
    "(bool),criticality_tier=safety|process_critical|process_flexible|deferrable,p_nom "
    "(MW),p_nom_extendable (bool),p_nom_min (MW),p_nom_max (MW),overnight_cost (EUR/MW),lifetime "
    "(years),build_year (year),discount_rate (fraction),fom_cost (EUR/MW/yr),marginal_cost "
    "(EUR/MWh),stand_by_cost (EUR/h),efficiency (fraction),efficiency2,efficiency3,efficiency4,p_min_pu "
    "(fraction),p_max_pu (fraction),committable (bool),ramp_limit_up (fraction),ramp_limit_down "
    "(fraction),min_up_time (h),min_down_time (h),rated_mva "
    "(MVA),harmonic_source=linear|6-pulse|12-pulse|18-pulse|active-front-end|phase-angle|burst|thyristor-stack,harmonic_spectrum,spectrum_post_filter"
    " (bool),flicker_step_mva (MVA),flicker_changes_per_min (1/min),single_phase_share "
    "(fraction),motor_kw (kW),start_method=dol|soft|vfd,i_start_pu (pu),outage_rate_value "
    "(fraction),outage_rate_basis=FOR|EFORd,mttr_hours (h); derived: "
    "capital_cost,start_up_cost,throughput_unit,specific_energy_kwh_per_t.\n"
    "Load: carrier,bus*,p_set (MW),q_set (MVAr),sign.\n"
    "ShuntImpedance: bus*,g (S),b (S).\n"
    "StorageUnit: carrier,bus*,p_nom (MW),max_hours (h),p_nom_extendable (bool),p_nom_min (MW),p_nom_max "
    "(MW),inv_power_overnight (EUR/MW),inv_power_lifetime (years),inv_energy_overnight "
    "(EUR/MWh),inv_energy_lifetime (years),build_year (year),discount_rate (fraction),inv_power_fom_share"
    " (fraction),inv_energy_fom_share (fraction),marginal_cost (EUR/MWh),efficiency_store "
    "(fraction),efficiency_dispatch (fraction),standing_loss (fraction),cyclic_state_of_charge "
    "(bool),state_of_charge_initial (MWh),inflow (MW),outage_rate_value "
    "(fraction),outage_rate_basis=FOR|EFORd,mttr_hours (h); derived: lifetime,capital_cost,fom_cost. "
    "Investment parts: inv_power_* is the power part (EUR/MW); inv_energy_* is the energy part (EUR/MWh, "
    "× max_hours).\n"
    "Store: carrier,bus*,e_nom (MWh),e_nom_extendable (bool),e_nom_min (MWh),e_nom_max "
    "(MWh),overnight_cost (EUR/MWh),lifetime (years),build_year (year),discount_rate (fraction),fom_cost "
    "(EUR/MWh/yr),marginal_cost (EUR/MWh),e_min_pu (fraction),e_max_pu (fraction),e_cyclic "
    "(bool),e_initial (MWh),standing_loss (fraction),outage_rate_value "
    "(fraction),outage_rate_basis=FOR|EFORd,mttr_hours (h); derived: capital_cost.\n"
    "Transformer: bus0*,bus1*,s_nom (MVA),s_nom_extendable (bool),s_nom_min (MVA),s_nom_max "
    "(MVA),overnight_cost (EUR/MVA),lifetime (years),build_year (year),discount_rate (fraction),fom_cost "
    "(EUR/MVA/yr),v_nom_0 (kV),v_nom_1 (kV),r (pu),x (pu),tap_ratio,phase_shift (°); derived: "
    "capital_cost."
)
# END GENERATED


if __name__ == "__main__" and "--write" in sys.argv:
    _write()
