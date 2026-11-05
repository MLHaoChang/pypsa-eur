// RP-04 end-to-end check (Playwright, Chromium, synthetic source) against the
// real backend (:8000) and Vite (:5173). Seeds fixture `E2E Prof` (g_wind and
// g_pv at 8E 50N, g_nocoord on a 0/0 bus, hourly 2023), then:
//   1. one click generates for the ready set; undo restores the prior p_max_pu
//      (one step per technology, owner RP04-UNDO-1);
//   2. one click again, g_wind shows a profile, the banner lists g_nocoord;
//   3. Set coordinates -> the row turns ready -> its own Generate -> profile.
// Run: NODE_PATH=$(npm root -g) node rp04-e2e.js  (screenshots land next to it)
const { chromium } = require('playwright'); const fs = require('fs'); const assert = require('assert')
const OUT = __dirname + '/'; const API = 'http://127.0.0.1:8000/api'; const APP = 'http://127.0.0.1:5173/'
const PW = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
;(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1400, height: 900 } })
  const lg = await p.request.post(API + '/auth/login', { data: { email: 'admin@example.com', password: PW } })
  const H = { 'X-CSRF-Token': (await lg.json()).csrf_token }
  const call = async (m, u, data) => { const r = await p.request.fetch(API + u, { method: m, data, headers: H }); assert(r.ok(), m + ' ' + u + ' ' + r.status()); return r }
  await call('POST', '/network/reset')
  await call('POST', '/network/buses', { name: 'b_de', x: 8.0, y: 50.0 })
  await call('POST', '/network/buses', { name: 'b_zero', x: 0, y: 0 })
  await call('POST', '/network/snapshots', { start: '2023-01-01 00:00', end: '2023-12-31 23:00', freq: 'h' })
  for (const [n, bus, c, pn] of [['g_wind', 'b_de', 'wind', 50], ['g_pv', 'b_de', 'solar', 30], ['g_nocoord', 'b_zero', 'solar', 20]])
    await call('POST', '/network/generators', { name: n, bus, carrier: c, p_nom: pn })
  await call('POST', '/projects/E2E%20Prof?force=true')
  const cols = async () => (await (await call('GET', '/network/timeseries/generators/p_max_pu')).json()).columns || []
  const before = await cols()
  await p.goto(APP); await p.waitForTimeout(3000)
  if (await p.getByRole('button', { name: 'Sign in' }).count()) {
    await p.getByRole('button', { name: 'Sign in' }).click()
    await p.fill('input[type=email]', 'admin@example.com'); await p.fill('input[type=password]', PW)
    await p.keyboard.press('Enter'); await p.waitForTimeout(6000)
  }
  await p.locator('button,a,[role=button]', { hasText: /Open E2E Prof/ }).first().dispatchEvent('click', {}, { timeout: 5000 }).catch(() => {}); await p.waitForTimeout(4000)
  const snap = async n => { await p.screenshot({ path: OUT + 'rp04-e2e-' + n + '.png' }); console.log('shot', n) }
  await p.getByRole('button', { name: 'Expert', exact: true }).click(); await p.waitForTimeout(1500)
  await p.getByText('Time Series', { exact: true }).first().click(); await p.waitForTimeout(2500)
  await p.getByRole('button', { name: /Renewables/ }).first().click(); await p.waitForTimeout(2500)
  const all = async () => { await p.getByTestId('generate-all-ready').click(); await p.getByTestId('generate-notice').waitFor({ timeout: 180000 }); await p.waitForTimeout(1500) }
  // 1. undo restores every prior p_max_pu
  await all(); assert.deepStrictEqual((await cols()).sort(), ['g_pv', 'g_wind'])
  await p.locator('body').click({ position: { x: 700, y: 300 } })
  for (let i = 0; i < 2; i++) { await p.keyboard.press('Control+z'); await p.waitForTimeout(2500) }
  assert.deepStrictEqual((await cols()).sort(), before.sort(), 'undo x2 restores the prior p_max_pu')
  const kinds = async () => Object.fromEntries((await (await call('GET', '/projects/E2E%20Prof/profiles/readiness')).json()).generators.map(r => [r.generator, r.profile]))
  console.log('readiness after undo', JSON.stringify(await kinds()))
  await p.getByText('2 ready').waitFor({ timeout: 10000 }) // the Renewables tab re-reads readiness after an undo
  await snap('1-undone')
  // 2. one click, chart for g_wind, banner lists the 0/0 generator
  await all()
  await p.getByText('g_wind', { exact: true }).locator('visible=true').first().click(); await p.waitForTimeout(2500)
  assert(await p.locator('svg, canvas').count() > 0); await snap('2-wind-chart-and-banner')
  assert((await p.getByTestId('missing-coordinates-banner').innerText()).includes('g_nocoord'))
  // 3. Set coordinates -> ready -> Generate -> profile
  await p.getByTestId('set-coordinates-g_nocoord').click()
  await p.getByTestId('coord-x-b_zero').fill('13.4'); await p.getByTestId('coord-y-b_zero').fill('52.5')
  await p.getByTestId('coord-save-b_zero').click()
  await p.getByTestId('missing-coordinates-banner').waitFor({ state: 'detached', timeout: 20000 }); await snap('3-row-ready')
  await p.getByTestId('generate-all-ready').click(); await p.waitForTimeout(500)
  await p.getByTestId('generate-notice').filter({ hasText: /generated and applied/ }).waitFor({ timeout: 180000 })
  assert((await cols()).includes('g_nocoord'), 'g_nocoord has a profile'); await snap('4-nocoord-generated')
  console.log('E2E OK'); await b.close()
})().catch(e => { console.error(e); process.exit(1) })
