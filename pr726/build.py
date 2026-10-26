"""PR #726 evidence data: four unbalance sites, imported and saved as projects through the API,
each with a published grid-code copy that sets unbalance_limit_pct 2.0 % (demo value)."""
import math, sys, os, json, yaml, pathlib
sys.path.insert(0, '/home/user/pypsa-eur/pypsa-gui/backend'); os.chdir('/home/user/pypsa-eur/pypsa-gui/backend')
import httpx
from tests.test_grid_readiness_unbalance import _site
from gridspine.templates.grid_codes import load_grid_code
out = os.path.dirname(os.path.abspath(__file__)) + '/'
cases = {"unb-pass": _site(0.5), "unb-warn": _site(1.0, sk=1000.0), "unb-empty": _site(math.nan),
         "unb-balanced": _site(0.0)}
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=120)
pw = open(os.path.expanduser('~/.local/share/PyPSA Studio/dev-admin-password')).read().strip()
r = c.post('/api/auth/login', json={'email': 'admin@example.com', 'password': pw}); r.raise_for_status()
h = {'X-CSRF-Token': r.json().get('csrf_token') or c.cookies.get('pypsa_gui_csrf')}
raw = load_grid_code('eu_rfg_dcc_ce', raw=True); raw['title'] = 'EU RfG/DCC CE (copy, unbalance)'
raw['unbalance_limit_pct'] = {'value': 2.0, 'clause': 'Demo negative-sequence planning level, not verified',
                              'source': 'code'}
p = {k: v for k, v in raw.items() if k != 'name'}
for name, n in cases.items():
    f = out + name + '.nc'; n.export_to_netcdf(f)
    print(name, 'import', c.post('/api/io/import/netcdf', files={'file': (name + '.nc', open(f, 'rb'))}, headers=h).status_code)
    r = c.post('/api/projects/' + name, headers=h); print(name, 'save', r.status_code, r.text[:120])
    base = '/api/campus-electrical/' + name + '/grid-codes'
    print(' draft', c.post(base + '/drafts', json={'profile_id': 'eu_unbalance', 'title': raw['title'], 'overwrite': True}, headers=h).status_code,
          c.put(base + '/drafts/eu_unbalance', json={'yaml': yaml.safe_dump(p, sort_keys=False, allow_unicode=True)}, headers=h).status_code)
    r = c.post(base + '/drafts/eu_unbalance/publish', json={'allow_unconfirmed': True}, headers=h); print(' publish', r.status_code, r.text[:160])
    r = c.get('/api/campus-electrical/' + name + '/grid-readiness', params={'profile': 'eu_unbalance'})
    row = [x for x in r.json()['rows'] if x['id'] == 'voltage_unbalance'] if r.status_code == 200 else r.text[:200]
    print(' row', r.status_code, json.dumps(row)[:300])
projs = c.get('/api/projects/').json()
pathlib.Path(out + 'projects.json').write_text(json.dumps({p['name']: p['id'] for p in projs if p['name'].startswith('unb-')}))
pathlib.Path(out + 'cookies.json').write_text(json.dumps(dict(c.cookies)))
