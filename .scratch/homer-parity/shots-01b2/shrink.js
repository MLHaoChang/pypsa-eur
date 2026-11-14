// Shrinks the dispatchable capacity of or-shots so a 100 % load reserve cannot be held.
const { request } = require('playwright')
;(async () => {
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173', storageState: __dirname + '/state.json' })
  const st = await api.storageState()
  const h = { 'X-CSRF-Token': st.cookies.find(c => c.name === 'pypsa_gui_csrf')?.value }
  const put = async (p, data) => { const r = await api.put(p, { headers: h, data }); console.log(p, r.status(), r.ok() ? '' : (await r.text()).slice(0, 200)) }
  for (const g of (await (await api.get('/api/network/generators')).json())) {
    if (['mainland_supply', 'diesel_1', 'diesel_2'].includes(g.name)) await put('/api/network/generators/' + g.name, { name: g.name, bus: g.bus, p_nom: 0.3 })
    else if (!g.p_nom_extendable) console.log('left', g.name, g.carrier, g.p_nom)
    else console.log('EXT', g.name, g.p_nom_max)
  }
  await put('/api/network/storage_units/island_battery', { name: 'island_battery', bus: 'island', p_nom: 0.3 })
  await put('/api/network/storage_units/bess_new', { name: 'bess_new', bus: 'island', p_nom_max: 0.3 })
})()
