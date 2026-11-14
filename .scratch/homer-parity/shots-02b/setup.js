const { request } = require('playwright')
const fs = require('fs')
const BASE = 'http://127.0.0.1:5173'
;(async () => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
  const api = await request.newContext({ baseURL: BASE })
  let r = await api.post('/api/auth/login', { data: { email: 'admin@example.com', password: pw } })
  console.log('login', r.status())
  const csrf = (await api.storageState()).cookies.find(c => c.name === 'pypsa_gui_csrf')?.value
  const h = { 'X-CSRF-Token': csrf }
  r = await api.post('/api/projects/from_template/eh_microgrid?name=rh-shots', { headers: h }); console.log('project', r.status())
  r = await api.post('/api/projects/rh-shots/activate', { headers: h }); console.log('activate', r.status())
  r = await api.post('/api/network/buses', { headers: h, data: { name: 'bus-diesel' } }); console.log('bus', r.status())
  r = await api.post('/api/network/generators', { headers: h, data: { name: 'diesel-02b', bus: 'bus-diesel', carrier: 'diesel', p_nom: 1, committable: true, marginal_cost: 10, om_cost_per_run_hour: 3, lifetime_run_hours: 15000 } })
  console.log('gen', r.status(), (await r.text()).slice(0, 400))
  r = await api.post('/api/network/generators', { headers: h, data: { name: 'diesel-uc-off', bus: 'bus-diesel', carrier: 'diesel', p_nom: 1, committable: false } }); console.log('gen2', r.status(), (await r.text()).slice(0, 300))
  r = await api.get('/api/network/asset_schema/Generator', { headers: h }); console.log('schema', r.status(), (await r.text()).includes('om_cost_per_run_hour'))
  await api.storageState({ path: __dirname + '/state.json' })
})()
