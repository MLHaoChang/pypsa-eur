const { request } = require('playwright')
;(async () => {
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173', storageState: __dirname + '/state.json' })
  const st = await api.storageState()
  const h = { 'X-CSRF-Token': st.cookies.find(c => c.name === 'pypsa_gui_csrf')?.value }
  let r = await api.put('/api/simulation/solver_config', { headers: h, data: { reserve_share_load: Number(process.argv[2]), voll: 0 } })
  console.log('put', r.status(), (await r.text()).slice(0, 100))
  r = await api.post('/api/simulation/run', { headers: h })
  console.log('run', r.status())
  for (let i = 0; i < 90; i++) {
    await new Promise(res => setTimeout(res, 2000))
    const s = await (await api.get('/api/simulation/status')).json()
    if (!['running', 'queued', 'pending'].includes(s.status)) { console.log('status', s.status, s.termination_condition || ''); break }
  }
  const raw = await (await api.get('/api/simulation/log_history')).text()
  const log = raw.replace(/\\n/g, '\n')
  console.log(log.split('\\n').filter(l => /RESERVE|reserve|INFEASIBLE/.test(l)).join('\n').slice(0, 1500))
})()
