r"""
Scaffold a data centre + waste-heat-recovery heat pump into the
currently-loaded PyPSA-GUI network via its REST API.

What it adds (idempotent — re-running updates names instead of duplicating):
  Buses:
    * <prefix>_lowT_heat   (waste-heat carrier from the data centre)
    * <prefix>_highT_heat  (high-T district heat sink)
  Links:
    * <prefix>_datacenter
        bus0 = <electrical bus>, bus1 = <prefix>_lowT_heat
        efficiency  = waste-heat fraction (default 0.95)
        p_nom = DC_MW, p_min_pu = p_max_pu = 1.0   (must-run / forced load)
        — acts like a Load on the electrical bus AND a forced heat source.
    * <prefix>_heatpump
        bus0 = <electrical bus>
        bus1 = <prefix>_highT_heat   (efficiency  = COP, e.g. 3 for COP=3 —
                                       total high-T heat delivered per unit
                                       electricity, including the lifted
                                       low-T contribution)
        bus2 = <prefix>_lowT_heat    (efficiency2 = -(COP - 1), e.g. -2 —
                                       EXTRACTS heat from the waste-heat
                                       bus; negative = sink in PyPSA's
                                       Link convention)
        — energy balance: 1 elec + (COP-1) low-T -> COP high-T (first law
          satisfied because eff + eff2 = COP - (COP-1) = 1).
        p_nom_extendable = True so the LP can size it.
  Generator (heat dump on the low-T bus):
    * <prefix>_lowT_dump
        p_nom = 10 x DC_MW, p_max_pu = 0, p_min_pu = -1, marginal_cost = 0
        — free sink. Without it the LP becomes infeasible whenever the
        district-heat demand drops below the data centre's waste-heat
        production (DC runs must-run, so the heat HAS to go somewhere).

Usage:
    pixi run python pypsa-gui/scripts/scaffold_dc_heatpump.py \\
        --elec-bus bus1 \\
        --dc-mw 50 \\
        --cop 3.0

Notes:
  - Run with --list-buses to see what electrical buses you can attach to.
  - Add a Load on <prefix>_highT_heat afterwards to drive demand for the
    upgraded heat (without one, the heat pump won't run).
"""

from __future__ import annotations

import argparse
import sys

import requests

BASE = "http://127.0.0.1:8000/api/network"
TIMEOUT = 60  # cascade-delete + lock contention on OneDrive paths can be slow


def _get(path: str) -> list[dict]:
    r = requests.get(f"{BASE}{path}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict) -> dict:
    r = requests.post(f"{BASE}{path}", json=payload, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else {}


def _put(path: str, payload: dict) -> dict:
    r = requests.put(f"{BASE}{path}", json=payload, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"PUT {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else {}


def _ensure_bus(name: str, carrier: str, x: float, y: float) -> None:
    """
    Create the bus if missing. If it already exists, leave it alone —
    re-running shouldn't disturb buses that other components depend on
    (deleting + recreating a bus cascades through every Link/Generator/
    Load wired to it, which is slow under the OneDrive-synced lock).
    """
    existing = {b["name"]: b for b in _get("/buses")}
    if name in existing:
        return
    _post("/buses", {
        "name": name, "v_nom": 1.0, "carrier": carrier, "x": x, "y": y,
        "country": "", "unit": "MWh_th", "control": "PQ", "sub_network": "",
    })


def _ensure_link(name: str, payload: dict) -> None:
    """
    PUT-update if the link already exists, POST-create otherwise.
    PUT is non-destructive (preserves any user edits to fields we don't
    send) — safer than DELETE + POST.
    """
    existing = {l["name"]: l for l in _get("/links")}
    if name in existing:
        _put(f"/links/{name}", {"name": name, **payload})
        return
    _post("/links", {"name": name, **payload})


def _ensure_generator(name: str, payload: dict) -> None:
    existing = {g["name"]: g for g in _get("/generators")}
    if name in existing:
        _put(f"/generators/{name}", {"name": name, **payload})
        return
    _post("/generators", {"name": name, **payload})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--elec-bus", help="Name of the electrical bus to attach the data centre + heat pump to.")
    p.add_argument("--dc-mw", type=float, default=50.0, help="Data-centre IT power (MW). Default 50.")
    p.add_argument("--cop", type=float, default=3.0, help="Heat-pump COP. Default 3.0 (efficiency1 = COP-1 = 2.0).")
    p.add_argument("--waste-eff", type=float, default=0.95, help="Data-centre waste-heat conversion. Default 0.95.")
    p.add_argument("--prefix", default="dc", help="Prefix for created assets (default 'dc' -> dc_datacenter, dc_heatpump, …).")
    p.add_argument("--list-buses", action="store_true", help="List existing buses and exit (no network changes).")
    args = p.parse_args(argv)

    try:
        buses = _get("/buses")
    except Exception as exc:
        print(f"Could not reach backend at {BASE} — is it running? ({exc})", file=sys.stderr)
        return 2

    if args.list_buses:
        print("Existing buses:")
        for b in buses:
            print(f"  - {b['name']}  (carrier={b.get('carrier','')}, v_nom={b.get('v_nom','')})")
        return 0

    if not args.elec_bus:
        print("Missing --elec-bus. Run with --list-buses to see available buses.", file=sys.stderr)
        return 2

    elec = next((b for b in buses if b["name"] == args.elec_bus), None)
    if elec is None:
        print(f"Electrical bus '{args.elec_bus}' not found in network.", file=sys.stderr)
        return 2

    cop = args.cop
    waste_eff = args.waste_eff
    pfx = args.prefix
    dc_mw = args.dc_mw

    if cop <= 1.0:
        print(f"COP must be > 1 (got {cop}).", file=sys.stderr)
        return 2

    lowt_name  = f"{pfx}_lowT_heat"
    hight_name = f"{pfx}_highT_heat"

    # Place the new heat buses just south-east of the chosen electrical bus
    # so they don't pile up on top of existing geometry.
    bx, by = float(elec.get("x") or 0.0), float(elec.get("y") or 0.0)

    print(f"Attaching to electrical bus '{args.elec_bus}' (x={bx:.4f}, y={by:.4f}).")
    print(f"DC IT power: {dc_mw} MW | waste-heat conversion: {waste_eff} | heat-pump COP: {cop}")
    print()

    print(f"  + bus {lowt_name}  (low-T waste-heat)")
    _ensure_bus(lowt_name,  carrier="heat", x=bx + 0.10, y=by - 0.05)
    print(f"  + bus {hight_name} (high-T district heat)")
    _ensure_bus(hight_name, carrier="heat", x=bx + 0.10, y=by - 0.10)

    print(f"  + link {pfx}_datacenter  (must-run, elec -> low-T heat, eff={waste_eff})")
    _ensure_link(f"{pfx}_datacenter", {
        "bus0": args.elec_bus, "bus1": lowt_name, "carrier": "datacenter",
        "efficiency": waste_eff,
        "p_nom": dc_mw, "p_nom_extendable": False,
        "p_min_pu": 1.0, "p_max_pu": 1.0,  # forced must-run; edit later via UI
        "marginal_cost": 0.0, "capital_cost": 0.0,
    })

    # Heat-pump efficiencies — first law: 1 elec + (COP-1) low-T -> COP high-T.
    #   efficiency  (= eff_hot)  = COP             (high-T delivered per elec)
    #   efficiency2 (= eff_cold) = -(COP - 1)      (low-T extracted per elec)
    # PyPSA Link convention: positive eff_n = source at bus_n, negative =
    # sink (heat removed). So a +ve eff and -ve eff2 makes the link CONSUME
    # both electricity (bus0) AND low-T heat (bus2), and DELIVER high-T
    # heat (bus1) — exactly the physics of a heat pump.
    eff_hot  = cop
    eff_cold = -(cop - 1.0)
    print(f"  + link {pfx}_heatpump    (elec + low-T -> high-T, eff={eff_hot:+.2f}, eff2={eff_cold:+.2f})")
    _ensure_link(f"{pfx}_heatpump", {
        "bus0": args.elec_bus, "bus1": hight_name, "bus2": lowt_name,
        "carrier": "heat-pump-waste",
        "efficiency":  eff_hot,        # = COP, to high-T bus
        "efficiency2": eff_cold,       # = -(COP-1), extracts from low-T bus
        "p_nom": 0.0, "p_nom_extendable": True,
        "p_nom_min": 0.0,
        "p_min_pu": 0.0, "p_max_pu": 1.0,
        "marginal_cost": 0.0,
        # Annualised €/MW. ~100k is a mid-range industrial HP CAPEX
        # (assumes ~1000 €/kW overnight, 20 yr life, 7 % discount). Switch
        # to overnight_cost on the asset for proper annuity via the GUI's
        # per-asset discount-rate annuity if you want realistic numbers.
        "capital_cost": 100_000.0,
    })

    dump_name = f"{pfx}_lowT_dump"
    print(f"  + generator {dump_name}  (free heat sink on low-T bus)")
    _ensure_generator(dump_name, {
        "bus": lowt_name, "carrier": "heat-dump",
        "p_nom": 10 * dc_mw,
        "p_max_pu": 0.0, "p_min_pu": -1.0,
        "marginal_cost": 0.0, "capital_cost": 0.0,
    })

    # Feasibility ceiling on the high-T side.
    # DC produces dc_mw * waste_eff MW of low-T heat every snapshot.
    # The heat pump needs (COP-1) units of low-T per (COP) units of high-T.
    # So the maximum continuous high-T load the system can serve from waste
    # heat alone = lowT_supply * COP / (COP - 1).
    lowT_supply = dc_mw * waste_eff
    highT_ceiling = lowT_supply * cop / (cop - 1.0)

    print()
    print("Done. Next steps:")
    print(f"  1. Add a Load on '{hight_name}' to drive district-heat demand")
    print( "     (otherwise the heat pump won't run — there's nothing pulling high-T heat).")
    print(f"     Feasibility ceiling at full DC dispatch: high-T Load <= {highT_ceiling:.1f} MW.")
    print(f"     ({lowT_supply:.1f} MW low-T supply × COP/(COP-1) = {lowT_supply:.1f} × {cop/(cop-1):.2f}.")
    print( "      Beyond this, the LP is infeasible unless you add an alternative")
    print(f"      heat source on '{lowt_name}' — e.g. ambient air via a second Link.)")
    print(f"  2. (Optional) Edit '{pfx}_datacenter' in the Properties panel to")
    print( "     replace its constant p_min_pu = p_max_pu = 1.0 with a time-varying")
    print( "     IT-load profile via the Time-series page.")
    print(f"  3. (Optional) Add a Store on '{lowt_name}' if you want the heat-pump")
    print( "     to buffer waste heat across hours instead of dumping it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
