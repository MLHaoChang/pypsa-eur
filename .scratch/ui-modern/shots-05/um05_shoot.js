// ui-modern 05 time-to-power strip shots. Needs API :8000 + Vite :5173 and a project "Alpha DC"
// (POST /api/projects/from_template/eh_datacenter?name=Alpha DC). Run from this directory.
// Mocks only GET **/api/results/headline_kpis* (route fulfil); everything else is the real backend.
const { chromium, request } = require('playwright')
const fs = require('fs')
const BASE = 'http://127.0.0.1:5173'
const card = (id, value, unit, extra = {}) => ({ id, value, unit, state: 'ready', reason: null, formula_id: null, detail: null, delta: null, trend: null, ...extra })
const base = () => [
  card('lcoe', 78.4, 'EUR/MWh'),
  card('system_cost', 41200000, 'EUR/yr'),
  card('new_build_capital', 128500000, 'EUR'),
  card('curtailed_share', 3.2, '%'),
  card('unserved_energy', 0.4, 'MWh'),
]
const ttp = { ready12: card('time_to_power', 12, 'months'),
  ready48: card('time_to_power', 48, 'months', { detail: 'First power may be as early as 2027-01-01.' }),
  unavailable: card('time_to_power', null, null, { state: 'unavailable', reason: 'The project start date is not set.' }) }
const VARIANTS = [
  ['six-ready12', [...base(), ttp.ready12]],
  ['six-ready48-detail', [...base(), ttp.ready48]],
  ['six-unavailable', [...base(), ttp.unavailable]],
  ['five-notile', base()],
]
;(async () => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
  const api = await request.newContext({ baseURL: BASE })
  console.log('login', (await api.post('/api/auth/login', { data: { email: 'admin@example.com', password: pw } })).status())
  await api.storageState({ path: 'state.json' })
  const browser = await chromium.launch()
  for (const [name, cards] of VARIANTS) for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ storageState: 'state.json', viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
    const page = await ctx.newPage()
    await page.route('**/api/results/headline_kpis*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ kind: 'energy_hub', cards, comparison: null }) }))
    await page.goto(BASE + '/')
    await page.locator('text=Alpha DC').first().waitFor({ timeout: 20000 })
    await page.locator('a,button,[role=button]').filter({ hasText: 'Alpha DC' }).first().click()
    await page.waitForTimeout(3000)
    await page.getByText(mode === 'guided' ? 'Guided' : 'Expert', { exact: true }).first().click().catch(() => console.log('mode click failed'))
    await page.waitForTimeout(800)
    if (mode === 'expert') { await page.getByRole('button', { name: 'Results', exact: true }).first().click() }
    else { await page.getByText('Results & reports', { exact: true }).first().click(); await page.waitForTimeout(2500); await page.getByText('Open the detail').first().click(); await page.waitForTimeout(2000); await page.getByText('Summary', { exact: true }).first().click() }
    await page.waitForTimeout(3500)
    const strip = page.locator('[data-testid="headline-strip"]')
    const tag = `um05-${name}-${mode}`
    await page.screenshot({ path: `${tag}.png` })
    console.log(tag, 'strips:', await strip.count(), strip && await strip.count() ? (await strip.first().innerText()).replace(/\n+/g, ' | ') + ' ' + JSON.stringify(await strip.first().boundingBox()) : '')
    await ctx.close()
  }
  await browser.close()
})()
