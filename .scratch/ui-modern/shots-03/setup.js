// Create and solve the two shot projects through the API.
const { request } = require('playwright')
const fs = require('fs')
;(async () => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173' })
  let r = await api.post('/api/auth/login', { data: { email: 'admin@example.com', password: pw } })
  console.log('login', r.status())
  const state = await api.storageState()
  const csrf = state.cookies.find(c => c.name === 'pypsa_gui_csrf')?.value
  const h = { 'X-CSRF-Token': csrf }
  for (const [tpl, name] of [['eh_datacenter', 'kpi-dc'], ['eh_microgrid', 'kpi-mg']]) {
    r = await api.post(`/api/projects/from_template/${tpl}?name=${name}`, { headers: h })
    console.log(tpl, r.status(), (await r.text()).slice(0, 120))
    r = await api.post('/api/simulation/queue', { headers: h, data: { project_id: name } })
    console.log('queue', r.status(), (await r.text()).slice(0, 160))
  }
  await api.storageState({ path: 'state.json' })
})()
