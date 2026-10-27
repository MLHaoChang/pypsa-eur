import sys, os, json, pypsa, httpx, pathlib
d = os.path.dirname(os.path.abspath(__file__)) + '/'
n = pypsa.Network(); n.set_snapshots(range(4))
n.add("Bus", "B1"); n.add("Bus", "B2")
n.add("Load", "L1", bus="B1", p_set=5)
n.add("Generator", "PV", bus="B1", p_nom=10, carrier="solar")
n.add("StorageUnit", "Battery", bus="B2", p_nom=10, max_hours=2)
n.add("Link", "tie", bus0="B1", bus1="B2", p_nom=10)
n.export_to_netcdf(d + 'ux.nc')
pw = open(os.path.expanduser('~/.local/share/PyPSA Studio/dev-admin-password')).read().strip()
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=120)
r = c.post('/api/auth/login', json={'email': 'admin@example.com', 'password': pw}); r.raise_for_status()
h = {'X-CSRF-Token': r.json().get('csrf_token') or c.cookies.get('pypsa_gui_csrf')}
print(c.post('/api/io/import/netcdf', files={'file': ('ux.nc', open(d + 'ux.nc', 'rb'))}, headers=h).status_code)
print(c.post('/api/projects/ux', headers=h).text[:100])
ps = c.get('/api/projects/').json(); print(ps if len(str(ps))<400 else str(ps)[:400])
pathlib.Path(d + 'cookies.json').write_text(json.dumps(dict(c.cookies)))
