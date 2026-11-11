const { request } = require('playwright')
;(async () => {
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173', storageState: 'state.json' })
  const csrf = (await api.storageState()).cookies.find(c => c.name === 'pypsa_gui_csrf')?.value
  const h = { 'X-CSRF-Token': csrf }
  const wait = async () => { for (let i = 0; i < 60; i++) {
    const s = await (await api.get('/api/simulation/status')).json()
    if (!s.running) return s
    await new Promise(r => setTimeout(r, 3000)) } }
  console.log('dc', JSON.stringify(await wait()).slice(0, 200))
  let r = await api.post('/api/projects/from_template/eh_microgrid?name=kpi-mg', { headers: h })
  console.log('mg', r.status(), (await r.text()).slice(0, 80))
  r = await api.post('/api/simulation/queue', { headers: h, data: { project_id: 'kpi-mg' } })
  console.log('queue', r.status())
  await new Promise(r => setTimeout(r, 4000))
  console.log('mg', JSON.stringify(await wait()).slice(0, 200))
  r = await api.get('/api/results/headline_kpis')
  console.log(r.status(), (await r.text()).slice(0, 900))
})()
