const { request } = require('playwright')
;(async () => {
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173', storageState: __dirname + '/state.json' })
  for (const p of ['/api/network/generators','/api/network/storage_units','/api/network/stores']) {
    const r = await api.get(p); console.log(p, r.status(), (await r.text()).slice(0, 3500))
  }
})()
