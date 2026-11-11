// Mocked-API harness (the repo's pattern: s6-shots.mjs MOCK_RESERVES; no in-src harness route exists).
//   node harness.mjs <project> <state> voltage|reserve     -> ../harness-loadflow.png / ../harness-reserve.png
// voltage: ac_pf status + a 2000-row hourly /api/results/voltages answered with made-up per-bus values, shown on Load flow > Full timeframe.
// reserve: s6-shots.mjs's made-up reserves payload at N=2000 hourly rows, shown on Operation > Storage cycling.
import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [project, state, what] = process.argv.slice(2)
const OUT = '/home/user/pypsa-eur/.scratch/ui-modern/shots-06b/', N = Number(process.env.N || 2000)
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 1700 }, deviceScaleFactor: 2, storageState: state })
const p = await ctx.newPage(); p.setDefaultTimeout(30000)
const iso = Array.from({ length: N }, (_, i) => new Date(Date.UTC(2030, 0, 1, i)).toISOString().slice(0, 19))
if (what === 'voltage') {
  await p.route('**/api/results/ac_pf/status**', r => r.fulfill({ json: { available: true, converged: true, converged_snapshots: N, total_snapshots: N } }))
  await p.route('**/api/results/lines**', r => r.fulfill({ json: { index: iso, columns: ['line_1'], data: iso.map((_, i) => [20 + 10 * Math.sin(i / 50)]) } }))
  await p.route('**/api/results/voltages**', r => r.fulfill({ json: { index: iso, columns: ['grid', 'bess'], data: iso.map((_, i) => [1 + 0.03 * Math.sin(i / 30), 1 - 0.02 * Math.sin(i / 17 + 1)]) } }))
} else {
  const wave = (a, bb, f) => iso.map((_, i) => Math.round((a + bb * Math.sin(i / f)) * 10) / 10)
  const line = (id, amount) => ({ period: null, contract_id: `reserve:${id}`, payer: 'tso', payee: 'site', value_stream: 'reserve_capacity', quantity_mwh: null, amount, flags: ['activation_not_valued'] })
  const json = { products: [{ id: 'afrr_up', kind: 'secondary', direction: 'up', sustain_h: 1, counterparty: 'tso', price_source: 'demo' }, { id: 'afrr_down', kind: 'secondary', direction: 'down', sustain_h: 1, counterparty: 'tso', price_source: 'user' }],
    snapshots: iso, storages: [{ name: 'bess_1', string: true, floor_mwh: 4, e_cap_mwh: 10, soc_mwh: wave(6.5, 1.5, 4), committed: { afrr_up: wave(2, 1, 3), afrr_down: wave(2, 0.8, 5) } }],
    lines: [line('afrr_up', 1200), line('afrr_down', 800)], flags: [], headroom_h_per_string: 1, currency: 'EUR', money_year: 2030,
    not_modelled: 'activation energy and its price, cycling wear and non-delivery penalties are not modelled: capacity payments only' }
  await p.route('**/api/results/reserves**', r => r.fulfill({ json }))
}
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500)
if (what === 'voltage') { await p.getByTestId('results-group-grid').first().click(); await p.waitForTimeout(1000); await p.getByTestId('results-cardtab-load_flow').first().click(); await p.waitForTimeout(2500); await p.getByRole('button', { name: 'Full timeframe', exact: true }).first().evaluate(e => e.click()) }
else { await p.getByTestId('results-group-capacity').first().click(); await p.waitForTimeout(1000); await p.getByTestId('results-cardtab-operation').first().click(); await p.waitForTimeout(1000); await p.getByRole('button', { name: 'Storage cycling', exact: true }).first().click() }
await p.waitForTimeout(6000)
const notes = p.getByTestId('thin-note'); const n = await notes.count(); console.log(what, 'notes', n)
for (let i = 0; i < n; i++) {
  console.log('  ', await notes.nth(i).innerText())
  const el = notes.nth(i).locator(process.env.UP || 'xpath=../..'); await el.scrollIntoViewIfNeeded(); await p.waitForTimeout(800)
  await el.screenshot({ path: `${OUT}harness-${what === 'voltage' ? 'loadflow' : 'reserve'}${n > 1 ? '-' + (i + 1) : ''}.png` })
}
if (!n) await p.screenshot({ path: `${OUT}miss-harness-${what}.png` })
await b.close()
