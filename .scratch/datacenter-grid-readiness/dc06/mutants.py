import pathlib, subprocess, sys
M = [
 ("floor term dropped", "services/solver/reserve_products.py", '- need >= floor', '>= floor'),
 ("soc(t-1) side dropped", "services/solver/reserve_products.py", '        m.add_constraints(prev - need >= floor, name=f"Reserve-floor-prev-{s}")\n', ''),
 ("p_max_pu dropped from (1)", "services/solver/reserve_products.py", 'p_nom * _da(avail, coord) <= 0', 'p_nom <= 0'),
 ("symmetric counted up only", "models/reserve_products.py", 'return self.direction in ("symmetric", "down")', 'return self.direction in ("down",)'),
 ("string up row reads p_max_pu(t)", "services/solver/reserve_products.py", 'if s in the_plan["strings"] else p_max[s])', 'if False else p_max[s])'),
 ("non-string reads the static bound", "services/solver/reserve_products.py", 'if s in the_plan["strings"] else p_max[s])', 'if True else p_max[s])'),
 ("initial soc decayed (hand-rolled roll)", "services/solver/reserve_products.py", '.where(~_da(_initial_at(n, s, sns), coord).astype(bool), 1.0)', ''),
 ("period weight dropped", "services/solver/reserve_products.py", 'w = hours * period', 'w = hours'),
 ("eta on wrong side", "services/solver/reserve_products.py", 'room = (r_dn * d_dn).sum("reserve") * es', 'room = (r_dn * d_dn).sum("reserve") / es'),
 ("weights from generators", "services/solver/reserve_products.py", 'hours = n.snapshot_weightings.objective.loc[sns]', 'hours = n.snapshot_weightings.generators.loc[sns]'),
 # DC-06 (2): settlement, floor sources, headroom, ledger, objective bridge
 ("period_w added to settlement", "services/results/billing.py", 'amount = float((h[sel] * pr * r).sum())', 'amount = float((h[sel] * _period_w.to_numpy()[sel] * pr * r).sum())'),
 ("settlement on a stale frame", "services/results/billing.py", 'RP.solved(n, RP.inputs_digest(products, floors))', 'RP.solved(n)'),
 ("unsolved settles to 0", "services/results/billing.py", '            amount = None\n            if not why:', '            amount = 0.0 if why else None\n            if not why:'),
 ("UPS floor 0 without a critical load", "services/solver/reserve_products.py", 'if named and np.size(critical)', 'if np.size(critical)'),
 ("headroom raises the floor", "services/solver/reserve_products.py", 'sizing.get("e_backup_usable_mwh_per_string", sizing.get("e_usable_mwh_per_string"))', 'sizing.get("e_usable_mwh_per_string")'),
 ("headroom bought without capex", "services/adequacy/dc_backup.py", 'nameplate = usable / (b.depth_of_discharge * keep)', 'nameplate = backup_usable / (b.depth_of_discharge * keep)'),
 ("reconciliation without the reserve bridge", "services/commercial/participants.py", 'for s in inputs.reserve:\n                # Earned', 'for s in []:\n                # Earned'),
 ("objective bridge ignores reserve", "services/results/objective_decomposition.py", '"lp_basis_total": lp_basis - reserve', '"lp_basis_total": lp_basis'),
]
tests = sys.argv[1:] or ["tests/test_reserve_products.py"]
for name, f, a, b in M:
    p = pathlib.Path(f); src = p.read_text()
    if src.count(a) < 1:
        print(f"SKIP {name}: anchor count {src.count(a)}"); continue
    p.write_text(src.replace(a, b))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", *tests], capture_output=True, text=True, timeout=500)
        print(("RED  " if r.returncode else "ALIVE"), name)
    finally:
        p.write_text(src)
