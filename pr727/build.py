import math, sys, os, json
sys.path.insert(0, '/home/user/pypsa-eur/pypsa-gui/backend')
os.chdir('/home/user/pypsa-eur/pypsa-gui/backend')
import httpx
from tests import test_grid_readiness as T
out = '/tmp/claude-0/-home-user-pypsa-eur/1efa3d9c-8802-5356-b9c0-22c26739bea3/scratchpad/s/'
cases = {"flk-pass": T._flicker_site(step=10.0), "flk-warn": T._flicker_site(),
         "flk-nostep": T._flicker_site(step=math.nan), "flk-nosk": T._flicker_site(sk=math.nan)}
base = 'http://127.0.0.1:8000'
pw = open(os.path.expanduser('~/.local/share/PyPSA Studio/dev-admin-password')).read().strip()
c = httpx.Client(base_url=base, timeout=120)
r = c.post('/api/auth/login', json={'email': 'admin@example.com', 'password': pw}); r.raise_for_status()
h = {'X-CSRF-Token': r.json().get('csrf_token') or c.cookies.get('pypsa_gui_csrf')}
for name, n in cases.items():
    f = out + name + '.nc'
    n.export_to_netcdf(f)
    r = c.post('/api/io/import/netcdf', files={'file': (name + '.nc', open(f, 'rb'))}, headers=h)
    print(name, 'import', r.status_code)
    r = c.post('/api/projects/' + name, headers=h)
    print(name, 'save', r.status_code, r.text[:150])
print(c.get('/api/projects/').json()[:6])
c.cookies.jar  # noqa
import pathlib
pathlib.Path(out+'cookies.json').write_text(json.dumps(dict(c.cookies)))
