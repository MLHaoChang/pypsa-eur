"""Publish pq_demo, point the campus settings at it, and give the process units harmonic and unbalance inputs.
argv[1]: the share for the second process unit ("none" leaves it unset, for the unknown row)."""
import json, os, sys, glob, httpx, pypsa
B = "http://127.0.0.1:8000/api"
s = httpx.Client(timeout=300)
pw = open(os.path.expanduser("~/.local/share/PyPSA Studio/dev-admin-password")).read().strip()
s.post(f"{B}/auth/login", json={"email": "admin@example.com", "password": pw})
if s.cookies.get("pypsa_gui_csrf"): s.headers["X-CSRF-Token"] = s.cookies.get("pypsa_gui_csrf")
r = s.post(f"{B}/campus-electrical/dcsurf-demo/grid-codes/drafts/pq_demo/publish", json={"allow_unconfirmed": True})
print("publish", r.status_code, r.text[:150])
pd_ = glob.glob(os.path.expanduser("~/Documents/PyPSA Studio/Projects/*/dcsurf-demo"))[0]
st = os.path.join(pd_, "campus_electrical", "settings.json")
cur = json.load(open(st)) if os.path.exists(st) else {}
cur["profile"] = "pq_demo"; json.dump(cur, open(st, "w"))
n = pypsa.Network(os.path.join(pd_, "network.nc"))
proc = [l for l in n.links.index if bool(n.links.get("eh_process", {}).get(l, False))]
if not proc:  # the data-centre template has no process Links: add two drives on the MV bus
    for name, p in (("chiller_drives", 12.0), ("cooling_pumps", 6.0)):
        n.add("Link", name, bus0="dc_mv", bus1="it_bus", p_nom=p, efficiency=1.0)
        n.links.loc[name, "eh_process"] = True
    proc = ["chiller_drives", "cooling_pumps"]
print("process links", proc, "sk", n.buses.get("eh_sk_mva", None) is not None and n.buses["eh_sk_mva"].tolist())
share = sys.argv[1] if len(sys.argv) > 1 else "0.5"
for i, l in enumerate(proc):
    n.links.loc[l, "rated_mva"] = 1.25 * float(n.links.at[l, "p_nom"])
    n.links.loc[l, "harmonic_source"] = "6-pulse"
    n.links.loc[l, "single_phase_share"] = (None if share == "none" and i == len(proc) - 1 else float(share if share != "none" else 0.5))
n.export_to_netcdf(os.path.join(pd_, "network.nc"))
print("units", n.links.loc[proc, ["rated_mva", "harmonic_source", "single_phase_share"]].to_dict("index"))
