import sys, os, json, yaml, httpx
sys.path.insert(0, '/home/user/pypsa-eur/pypsa-gui/backend'); os.chdir('/home/user/pypsa-eur/pypsa-gui/backend')
from gridspine.templates.grid_codes import load_grid_code
S = os.environ['S'] + '/'
ids = json.load(open(S + 'projects.json'))
ck = json.load(open(S + 'cookies.json'))
pw = open(os.path.expanduser('~/.local/share/PyPSA Studio/dev-admin-password')).read().strip()
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=120)
r = c.post('/api/auth/login', json={'email': 'admin@example.com', 'password': pw}); r.raise_for_status()
h = {'X-CSRF-Token': r.json().get('csrf_token') or c.cookies.get('pypsa_gui_csrf')}
raw = load_grid_code('eu_rfg_dcc_ce', raw=True); raw['title'] = 'EU RfG/DCC CE (copy)'
p = {k: v for k, v in raw.items() if k != 'name'}
for nm in ('flk-warn',):
    base = '/api/campus-electrical/' + nm + '/grid-codes'
    print(c.post(base + '/drafts', json={'profile_id': 'eu_flicker', 'title': 'EU RfG/DCC CE (copy)', 'overwrite': True}, headers=h).status_code)
    print(c.put(base + '/drafts/eu_flicker', json={'yaml': yaml.safe_dump(p, sort_keys=False, allow_unicode=True)}, headers=h).status_code)
