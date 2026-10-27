import json, sys
B={json.loads(l)["case"]:json.loads(l) for l in open(sys.argv[1])}
A={json.loads(l)["case"]:json.loads(l) for l in open(sys.argv[2])}
F=1.2573346011408
rows=[]
def r(name,metric,b,a):
    if b is None and a is None: return
    ratio = (a/b if isinstance(b,(int,float)) and isinstance(a,(int,float)) and b not in (0,) else None)
    fmt=lambda v: f"{v:,.2f}" if isinstance(v,float) else str(v)
    rows.append(f"| {name} | {metric} | {fmt(b)} | {fmt(a)} | {'' if ratio is None else f'{ratio:.9f}'} |")
order=["eh_microgrid","eh_microgrid_staged","eh_datacenter","eh_datacenter_staged","eh_h2_hub","eh_h2_hub_staged","eh_ammonia_staged","eh_ipp_hybrid"]+[k for k in B if k.startswith("eh_site:")]
for c in order:
    for m in ("objective","npv","lcoe_nominal","lcoe_real","currency_year"):
        if m in B[c] or m in A[c]: r(c,m,B[c].get(m),A[c].get(m))
for t in ("gas-genset","fuel-cell","battery"):
    r("redundancy",f"{t} annualised EUR/MW/yr",B["redundancy"][t]["annualised_cost_eur_per_mw_yr"],A["redundancy"][t]["annualised_cost_eur_per_mw_yr"])
r("redundancy","pack version",B["redundancy"]["version"],A["redundancy"]["version"])
r("study","pack version",B["study:pack"]["version"],A["study:pack"]["version"])
r("study","currency_year",B["study:pack"]["finance"]["currency_year"],A["study:pack"]["finance"]["currency_year"])
for k in ("none","bess_2h","bess_pv_2h"):
    for m in ("objective","battery_p_nom_mw","pv_p_nom_mw","bill_baseline","bill_option","savings_annual","npv","levelised_cost"):
        b,a=B["study:golden_s5"][k].get(m),A["study:golden_s5"][k].get(m)
        if b is not None or a is not None: r(f"study golden_s5 {k}",m,b,a)
for t in ("de_industrial_illustrative","tou_reference_illustrative"):
    for k in ("none","bess_2h","bess_pv_2h"):
        r(f"seed bill {t} {k}","annual bill",B["study:seed_bills"][t][k]["annual_bill"],A["study:seed_bills"][t][k]["annual_bill"])
print("| case | metric | before (master 1d80850) | after | after/before |\n|---|---|---|---|---|")
print("\n".join(rows))
