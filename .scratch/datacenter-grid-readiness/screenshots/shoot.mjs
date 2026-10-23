// Screenshots for PR #659. Run with the vite dev server on :5199 serving harness.html (see README.md).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = new URL('.', import.meta.url).pathname
const bus = (name, poc, sk) => ({ name, v_nom: 110, carrier: 'AC', x: 10, y: 50, country: 'DE', unit: '', control: 'PQ', sub_network: '', eh_poc: poc, ...(sk != null ? { eh_sk_mva: sk } : {}) })
const BUSES = {
  one: [bus('grid', true, 250), bus('it', false, 9)],
  two: [bus('grid_a', true, 300), bus('grid_b', true, 500), bus('it', false, 9)],
  unset: [bus('grid', true, null)],
  none: [bus('it', false, null)],
}
const setting = { item: 'fault_level', user: null, default: { value: null, source: 'assumed', origin: 'placeholder' } }
const READINESS = { rows: [{ id: 'scr', requirement: 'Short-circuit ratio at the PoC', limit: 3, unit: 'SCR', source: 'the template',
  project_value: 8.3, verdict: 'pass', assumed: true, note: null, section: 'connection', setting,
  target: { section: 'connection', panel: null, component: 'Bus', entity: 'grid', field: 'eh_sk_mva' } }] }
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
async function page(buses) {
  const p = await (await browser.newContext({ viewport: { width: 900, height: 700 } })).newPage()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/network/buses') return route.fulfill({ json: BUSES[buses] })
    if (path.endsWith('/grid-readiness')) return route.fulfill({ json: READINESS })
    if (path === '/api/network/asset_schema/Bus') return route.fulfill({ json: { component: 'Bus', groups: [] } })
    if (path.startsWith('/api/network/catalog/')) return route.fulfill({ json: { component: 'Bus', attributes: [] } })
    if (path.includes('/provenance/')) return route.fulfill({ json: { component: 'Bus', name: 'x', fields: {} } })
    if (path.startsWith('/api/network/')) return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  return p
}
for (const mode of ['expert', 'guided']) {
  const shot = async (p, name) => { await p.waitForTimeout(700); await p.screenshot({ path: `${OUT}${name}-${mode}.png` }) }
  for (const [n, b] of [['01-limits-one-poc', 'one'], ['02-limits-two-poc', 'two'], ['03-limits-unset', 'unset'], ['04-limits-no-poc', 'none']]) {
    const p = await page(b); await p.goto(`${BASE}?view=limits&mode=${mode}`); await shot(p, n)
  }
  let p = await page('one'); await p.goto(`${BASE}?view=limits&mode=${mode}`)
  await p.getByLabel('Fault level value').fill('0'); await shot(p, '05-limits-invalid')
  p = await page('one'); await p.goto(`${BASE}?view=props&mode=${mode}&bus=grid`)
  await p.getByRole('button', { name: /edit bus/i }).click(); await shot(p, '06-props-poc-readonly')
  p = await page('one'); await p.goto(`${BASE}?view=props&mode=${mode}&bus=it`)
  await p.getByRole('button', { name: /edit bus/i }).click(); await shot(p, '07-props-nonpoc-editable')
  if (mode === 'guided') {
    p = await page('one'); await p.goto(`${BASE}?view=drawer&mode=guided`)
    await p.getByRole('button', { name: /edit bus/i }).click()
    await p.getByRole('button', { name: /Edit in Connection/ }).click(); await shot(p, '08-drawer-after-link')
  }
}
await browser.close()
