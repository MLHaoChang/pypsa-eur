"""
Shared fixtures for the wired EH stages (frontier / mc_certify / fmea_top /
LCOH) — one network shape used by the pytest files AND the QA driver
``tests/qa_eh_reference_design.py`` so the two cannot drift.

Every generator carries occurrence data, so the sequential MC and the COPT
have a non-empty fleet to sample; the import Link carries occurrence data too,
so the Class-B Link sweep has exactly one contingency. Snapshot weightings
sum to one year (``horizon_years == 1``), so LOLE is on the annual basis a
``target_lole_h`` is written against.

The peaker's marginal cost sits ABOVE the VOLL these tests configure, so the
ENS cap BINDS (shedding is cheaper than the peaker up to the cap) — a
frontier over a non-binding target is a flat line and proves nothing.
"""
from __future__ import annotations

import pandas as pd
import pypsa

HOURS = 8
#: VOLL the fixtures are meant to be solved with (peaker mc > VOLL → cap binds).
VOLL = 150.0


def certifiable_weak_network() -> pypsa.Network:
    """
    weak_flexible-shaped hub: PoC import Link (eh_role), critical bus,
    two occurrence-bearing local units, a grid supply behind the PoC.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    for c in ("gas", "AC"):
        n.add("Carrier", c)
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "hub_load", bus="hub",
          p_set=pd.Series([90.0, 100.0, 110.0, 120.0, 110.0, 100.0, 90.0, 80.0],
                          index=n.snapshots))
    n.add("Generator", "base", bus="hub", carrier="gas", p_nom=60.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "peaker", bus="hub", carrier="gas", p_nom=40.0,
          marginal_cost=300.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="gas", p_nom=200.0,
          marginal_cost=5.0, outage_rate_value=0.02,
          outage_rate_basis="EFORd", mttr_hours=100.0)
    n.add("Link", "import_poc", bus0="grid", bus1="hub", p_nom=100.0,
          p_nom_extendable=False, p_nom_max=100.0, efficiency=1.0,
          carrier="AC", outage_rate_value=0.03, outage_rate_basis="FOR",
          mttr_hours=48.0)
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    n.buses["eh_poc"] = False
    n.buses.at["grid", "eh_poc"] = True
    n.buses["eh_critical"] = False
    n.buses.at["hub", "eh_critical"] = True
    return n


def islanded_certify_network() -> pypsa.Network:
    """
    One bus, two occurrence-bearing units that together barely cover the
    load: the loss of either sheds, so the MC LOLE is hundreds of hours and a
    3 h/yr target FAILS while a loose one passes.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = 8760.0 / HOURS
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "base", bus="b", carrier="gas", p_nom=60.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=45.0,
          marginal_cost=300.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=20.0)
    # The off_grid pack needs an identifiable PoC Link to island (spec §6);
    # nothing sits behind it, so the MC's single-area fleet is the two units.
    n.add("Carrier", "AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Link", "import_poc", bus0="grid", bus1="b", p_nom=50.0,
          efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import_poc", "eh_role"] = "grid_import"
    return n


def no_occurrence_network() -> pypsa.Network:
    """
    The P1.5 study fixture shape: ENS binds, but NO generator carries
    occurrence data — asset values absent AND a carrier with no library
    default (``gas`` HAS one, which is why the P1.5 fixture cannot be reused
    here) — so the sampled fleet is empty and certification cannot run.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "custom_fuel")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="custom_fuel",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="custom_fuel",
          p_nom=40.0, marginal_cost=200.0)
    return n


def electrolyser_network() -> pypsa.Network:
    """
    Electrical hub + an electrolyser Link feeding an H₂ Load, so
    ``compute_lcoh`` has a consuming electrolyser to price.
    """
    n = certifiable_weak_network()
    n.add("Carrier", "H2")
    n.add("Carrier", "electrolysis")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "h2_demand", bus="h2", carrier="H2", p_set=10.0)
    n.add("Link", "electrolyser", bus0="hub", bus1="h2", p_nom=20.0,
          efficiency=0.7, carrier="electrolysis", marginal_cost=1.0,
          capital_cost=1000.0)
    n.links.at["electrolyser", "eh_role"] = ""
    return n
