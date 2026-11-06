import pathlib, subprocess, sys
M = [
 ("floor term dropped", "services/solver/reserve_products.py", '- need >= floor', '>= floor'),
 ("soc(t-1) side dropped", "services/solver/reserve_products.py", '        m.add_constraints(prev - need >= floor, name=f"Reserve-floor-prev-{s}")\n', ''),
 ("p_max_pu dropped from (1)", "services/solver/reserve_products.py", 'p_nom * _da(avail, coord) <= 0', 'p_nom <= 0'),
 ("symmetric counted up only", "models/commercial.py", 'return self.direction in ("symmetric", "down")', 'return self.direction in ("down",)'),
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
 ("reserve row mapping dropped", "services/finance/revenue_streams.py", '    "ancillary": "reserve",\n', ''),
 ("reserve escalates at another class", "services/finance/cashflow.py", '"ancillary": "export"', '"ancillary": "opex"'),
 ("objective bridge ignores reserve", "services/results/objective_decomposition.py", '"lp_basis_total": lp_basis - reserve', '"lp_basis_total": lp_basis'),
 # DC-06 (3): binding, currency gate, guards, typed refusals
 ("currency gate dropped", "services/commercial/binding.py", '        clash = reserve_price_currency_mismatch(p, have, currency)', '        clash = None'),
 ("no unit is not the stored EUR default", "services/commercial/binding.py", '    if not unit:\n        return "EUR"', '    if not unit:\n        return None'),
 ("unread currency left unchecked", "services/commercial/binding.py", '            if currency:\n                raise BindingRefusal(409, CURRENCY_MISMATCH, (', '            if False:\n                raise BindingRefusal(409, CURRENCY_MISMATCH, ('),
 ("preflight currency re-check dropped", "services/commercial/preflight.py", '    found += _reserve_currency_findings(n, cfg, currency)', '    pass'),
 ("coverage gap accepted", "services/commercial/binding.py", '        if values.isna().any():\n            raise BindingRefusal(422, code,', '        if False:\n            raise BindingRefusal(422, code,'),
 ("prices not written", "services/commercial/binding.py", '        _write_reserve_prices(n, reserve_prices, reserve_currency)', '        pass'),
 ("sustain_h > 0 dropped", "models/commercial.py", 'sustain_h: float = Field(gt=0, le=24', 'sustain_h: float = Field(le=24'),
 ("empty products dumped", "models/commercial.py", '            out.pop("reserve_products", None)', '            pass'),
 ("stale floor after a spec edit", "services/solver/reserve_products.py", '    backup = spec.backup\n', '    backup = derive_backup.__dict__.setdefault(str(project_dir), spec.backup)\n'),
 ("run starts despite a refusal", "routers/simulation.py", '        if _refusal:\n            raise HTTPException(422, _refusal)', '        pass'),
]
tests = sys.argv[1:] or ["tests/test_reserve_products.py", "tests/test_reserve_binding.py", "tests/test_reserve_run_refusal.py"]
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
