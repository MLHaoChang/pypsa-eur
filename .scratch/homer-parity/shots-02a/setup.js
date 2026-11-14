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
  r = await api.post('/api/projects/from_template/eh_microgrid?name=fc-shots', { headers: h }); console.log('project', r.status())
  r = await api.post('/api/projects/fc-shots/activate', { headers: h }); console.log('activate', r.status())
  r = await api.post('/api/network/buses', { headers: h, data: { name: 'bus-diesel' } }); console.log('bus', r.status())
  r = await api.post('/api/network/generators', { headers: h, data: { name: 'diesel-02a', bus: 'bus-diesel', carrier: 'diesel', p_nom: 1, committable: true, fuel_price: 40, fuel_curve_slope: 0.25, fuel_curve_no_load: 0.05 } })
  console.log('gen', r.status(), (await r.text()).slice(0, 400))
  r = await api.post('/api/network/generators', { headers: h, data: { name: 'diesel-plain', bus: 'bus-diesel', carrier: 'diesel', p_nom: 1, committable: false, fuel_price: 40, fuel_curve_slope: 0.25, fuel_curve_no_load: 0 } }); console.log('gen2', r.status())
  await api.storageState({ path: __dirname + '/state.json' })
})()
