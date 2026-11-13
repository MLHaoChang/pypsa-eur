const { request } = require('playwright')
;(async () => {
  const api = await request.newContext({ baseURL: 'http://127.0.0.1:5173', storageState: __dirname + '/state.json' })
  const j = await (await api.get('/api/simulation/log_history')).json()
  const lines = (Array.isArray(j) ? j : (j.lines || j.log || [])).map(x => typeof x === 'string' ? x : JSON.stringify(x))
  console.log(lines.length, Object.keys(j).slice(0,5).join(','))
  console.log(lines.filter(l => /RENEWABLE|INFEASIBLE|rror|Validation failed|ERROR/.test(l)).join('\n').slice(0, 2500))
})()
