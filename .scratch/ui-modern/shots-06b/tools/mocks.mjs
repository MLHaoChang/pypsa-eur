// MOCKED payloads on the real app: node mocks.mjs <project> <state> <mode expert|guided> <what...>   what: flows | loadshape | lostload | reserve
// flows/loadshape: the real response with its series stretched to LEN hourly rows (the backend itself caps both below the 1500 thinning threshold:
//   energy_flows series = 400 buckets, load_shape week = 168 rows). lostload: a made-up 8760-row, 2-bus shed record.
import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [project, state, mode, ...whats] = process.argv.slice(2)
const LEN = Number(process.env.LEN || 2000), OUT = '/home/user/pypsa-eur/.scratch/ui-modern/shots-06b/'
const stamps = n => Array.from({ length: n }, (_, i) => new Date(Date.UTC(2030, 0, 1, i)).toISOString().slice(0, 19).replace('T', ' '))
const stretch = (a, n) => Array.from({ length: n }, (_, i) => a[Math.floor(i * a.length / n)] * (1 + 0.3 * Math.sin(i / 40)))
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: Number(process.env.VH || 1000) }, deviceScaleFactor: 2, storageState: state })
const p = await ctx.newPage(); p.setDefaultTimeout(30000)
await p.route('**/api/results/energy_flows**', async r => {
  const res = await r.fetch(); const j = await res.json()
  if (j.series?.index) { const s = j.series; s.index = stamps(LEN); for (const g of ['supply', 'demand']) for (const k of Object.keys(s[g])) s[g][k] = stretch(s[g][k], LEN) }
  await r.fulfill({ response: res, json: j })
})
await p.route('**/api/results/load_shape**', async r => {
  const res = await r.fetch(); const j = await res.json(); const w = j.week
  if (w?.status === 'ok') { w.index = stamps(LEN); for (const k of Object.keys(w.series)) if (w.series[k].status === 'ok') w.series[k].mw = stretch(w.series[k].mw, LEN); w.flagged = [5, 6, 7] }
  await r.fulfill({ response: res, json: j })
})
await p.route('**/api/results/lost_load**', async r => {
  const N = 8760, idx = Array.from({ length: N }, (_, i) => new Date(Date.UTC(2030, 0, 1, i)).toISOString().slice(0, 19))
  const data = idx.map((_, i) => [i % 400 < 6 ? 3 + 2 * Math.sin(i) : 0, i % 700 < 3 ? 1.5 : 0])
  await r.fulfill({ json: { index: idx, columns: ['bus_a', 'bus_b'], data, voll_applied: true, voll_eur_per_mwh: 10000, total_mwh: 1234.5, total_cost_eur: 12345000, bus_carriers: { bus_a: 'AC', bus_b: 'AC' }, shed_hours: 140 } })
})
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: mode === 'guided' ? 'Guided' : 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
if (mode !== 'guided') { await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500) }
else { await p.getByTestId('hub-rail-step-results').first().click(); await p.waitForTimeout(2500); await p.getByTestId('hub-chapter-operation-open').first().click(); await p.waitForTimeout(3500) }
const T = { flows: ['Energy flow', 'flows'], loadshape: ['Load shape', 'loadshape'], lostload: ['Lost load', 'lostload'] }
for (const w of whats) {
  const [label, name] = T[w]
  try {
    if (mode === 'guided') { await p.getByTestId('results-tab-' + w).first().click() }
    else { await p.getByTestId('results-group-capacity').first().click(); await p.waitForTimeout(1000); await p.getByTestId('results-cardtab-operation').first().click(); await p.waitForTimeout(1000); await p.getByRole('button', { name: label, exact: true }).first().click() }
    await p.waitForTimeout(6000)
    const notes = p.getByTestId('thin-note'); const n = await notes.count(); console.log(name, 'notes', n)
    for (let i = 0; i < n; i++) {
      console.log('  ', await notes.nth(i).innerText())
      const el = notes.nth(i).locator(process.env.UP || 'xpath=..'); await el.scrollIntoViewIfNeeded(); await p.waitForTimeout(800)
      await el.screenshot({ path: `${OUT}${process.env.PFX || 'mock'}-${name}-${mode}${n > 1 ? '-' + (i + 1) : ''}.png` })
    }
    if (!n) await p.screenshot({ path: `${OUT}miss-${name}-${mode}.png` })
  } catch (e) { console.log('FAIL', name, e.message.slice(0, 200)) }
}
await b.close()
