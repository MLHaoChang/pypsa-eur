// AA-17 2a before/after shots: a MIP solve's withheld economics read
// "Not available (integer solve)" instead of 0. Real app (sign-in, projects) plus
// route mocks for the results payloads. Two Vite servers: BEFORE_PORT (5175,
// the backend branch) and AFTER_PORT (5174, the PR head); one backend on :8000.
// "before" serves the same MIP numbers with 0.0 for the withheld figures and no
// price_limit / unavailable_reason (what master returned on a MIP).
// Results tabs Economics / Dispatch / Load flow are `expertOnly: true` in
// Results.tsx TABS, so Guided has no such tab: those three are Expert-only.
// Run: OUT=<dir> node shots.mjs [surface...]   (needs XDG_DATA_HOME of the backend for the dev password)
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)(process.env.PW_FROM || 'playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = process.env.OUT || HERE
const PORTS = { before: 5175, after: 5174 }
const SURFACES = process.argv.slice(2).length ? process.argv.slice(2) : ['compare', 'economics', 'dispatch', 'loadflow']
const ONLY = process.env.ONLY
const PROJ = { a: 'aa17-a', b: 'aa17-b' }
const problems = [], written = []

// ── payloads ────────────────────────────────────────────────────────────────
const pv = (total) => ({ total, by_period: {} })
const lpCarrier = {
  gas: { revenue_meur: pv(12), opex_meur: pv(5), gen_cost_meur: pv(5), storage_charge_cost_meur: pv(0), curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(3), dispatch_gwh: pv(100), lcoe_eur_per_mwh: pv(80) },
  battery: { revenue_meur: pv(4), opex_meur: pv(2), gen_cost_meur: pv(0), storage_charge_cost_meur: pv(2), curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(1), dispatch_gwh: pv(10), lcoe_eur_per_mwh: pv(300) },
}
const lcoh = { name: 'electrolysis', carrier: 'H2 electrolysis', output_gwh: pv(7), lcoh_eur_per_mwh: pv(90), capex_meur: pv(1), opex_meur: pv(0.1), input_cost_meur: pv(0.5) }
const summary = (variant, mip) => {
  if (!mip) return { project: '', has_solve: true, periods: [], economics: { available: true, by_carrier: lpCarrier, per_asset_lcoh: [lcoh] } }
  const bc = JSON.parse(JSON.stringify(lpCarrier))
  if (variant === 'after') {
    bc.gas.revenue_meur = null; bc.gas.storage_charge_cost_meur = null
    Object.assign(bc.battery, { revenue_meur: null, opex_meur: null, storage_charge_cost_meur: null, lcoe_eur_per_mwh: null })
    return { project: '', has_solve: true, periods: [], economics: { available: true, unavailable_reason: 'mip_duals_not_marginal', by_carrier: bc, per_asset_lcoh: [] } }
  }
  bc.gas.revenue_meur = pv(0); bc.battery.revenue_meur = pv(0)
  bc.gas.storage_charge_cost_meur = pv(0); bc.battery.storage_charge_cost_meur = pv(0)
  // opex / lcoe of the carrier holding a charge cost: master summed the zero charge cost.
  return { project: '', has_solve: true, periods: [], economics: { available: true, by_carrier: bc, per_asset_lcoh: [lcoh] } }
}
function assetEcon(variant) {
  const after = variant === 'after'
  const n = (v, z = 0) => (after ? null : z)
  return {
    currency: 'EUR', is_multi_period: false, capital_costs_available: true,
    ...(after ? { price_limit: 'mip_duals_not_marginal' } : {}),
    periods: [],
    generators: [{ name: 'Gas', bus: 'B1', carrier: 'gas', p_nom_opt_mw: 100, energy_mwh: 500, capacity_factor: 0.5, revenue_eur: n(), vom_cost_eur: 100, fixed_cost_eur: 900, fom_cost_eur: 0, net_profit_eur: after ? null : -1000, lcoe_eur_per_mwh: 2, avg_price_eur_per_mwh: n(), by_period: [] }],
    storage_units: [{ name: 'Bat', bus: 'B1', carrier: 'battery', p_nom_opt_mw: 10, max_hours: 4, energy_capacity_mwh: 40, round_trip_efficiency: 0.9, discharge_mwh: 50, charge_mwh: 60, discharge_revenue_eur: n(), charge_cost_eur: n(), vom_cost_eur: 0, fixed_cost_eur: 300, fom_cost_eur: 0, net_profit_eur: after ? null : -300, lcos_eur_per_mwh: n(), spread_eur_per_mwh: n(), avg_discharge_price_eur_per_mwh: n(), avg_charge_price_eur_per_mwh: n(), by_period: [] }],
    stores: [], links: [], reading_notes: [],
  }
}
const kpiRow = (variant, carrier, cap, e) => ({ component: 'Generator', carrier, capacity_mw: cap, energy_mwh: e, capacity_factor_pct: 50, curtailment_mwh: 0, curtailment_pct: 0, market_value_eur_per_mwh: variant === 'after' ? null : 0.0, revenue_eur: variant === 'after' ? null : 0.0 })
const carrierKpis = (variant) => ({ rows: [kpiRow(variant, 'gas', 100, 500), kpiRow(variant, 'solar', 60, 120)], ...(variant === 'after' ? { price_limit: 'mip_duals_not_marginal' } : {}) })

// ── harness ─────────────────────────────────────────────────────────────────
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
async function mock(ctx, variant) {
  await ctx.route(u => u.pathname === '/api/results/asset_economics', r => json(r, assetEcon(variant)))
  await ctx.route(u => u.pathname === '/api/results/carrier_kpis', r => json(r, carrierKpis(variant)))
  await ctx.route(u => u.pathname === '/api/results/economics_by_carrier', r => json(r, summary(variant, true).economics))
  await ctx.route(u => /\/api\/projects\/[^/]+\/results-summary$/.test(u.pathname), r => {
    const name = decodeURIComponent(r.request().url().match(/projects\/([^/]+)\/results-summary/)[1])
    const s = summary(variant, name === PROJ.b); s.project = name; return json(r, s)
  })
}
async function login(browser, base) {
  const { devCredentials } = await import('/home/user/pypsa-eur/pypsa-gui/frontend/scripts/dev-credentials.mjs')
  const ctx = await browser.newContext(); const p = await ctx.newPage()
  await p.goto(`${base}/login`)
  const c = devCredentials()
  await p.locator('#email').fill(c.email); await p.locator('#password').fill(c.password)
  await p.getByRole('button', { name: 'Sign in', exact: true }).click()
  await p.waitForURL(/\/projects/); await p.waitForLoadState('networkidle')
  for (const name of Object.values(PROJ)) {
    const made = await p.evaluate(async (name) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(x => x.name === name)) return 'exists'
      try { await projectsApi.createFromTemplate('eh_datacenter', name); return 'made' } catch (e) { return `failed: ${e.message}` }
    }, name)
    console.log(base, name, made)
  }
  const st = await ctx.storageState(); await ctx.close(); return st
}
async function open(browser, auth, variant, scheme, mode) {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: scheme })
  await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await ctx.addInitScript(({ project, scheme, mode }) => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', mode)
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:theme', scheme)
    localStorage.setItem('compare:a', 'aa17-a'); localStorage.setItem('compare:b', 'aa17-b'); localStorage.setItem('compare:tab', 'economics')
  }, { project: PROJ.a, scheme, mode })
  await mock(ctx, variant)
  const page = await ctx.newPage(); page.setDefaultTimeout(15000)
  page.on('pageerror', e => problems.push(`${variant}/${scheme}/${mode}: page error: ${e.message}`))
  return { ctx, page }
}
async function land(page, base) {
  await page.goto(`${base}/`); await page.waitForLoadState('networkidle')
  const resume = page.getByRole('link', { name: /Resume project/ })
  if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
  await page.waitForTimeout(2000)
  await page.evaluate(() => { for (const n of document.querySelectorAll('div[style*="z-index: 9999"]')) n.style.visibility = 'hidden' })
}
const showTab = (page, tab) => page.evaluate(async (tab) => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab(tab) }, tab)
async function shot(page, name, target) {
  const f = path.join(OUT, `${name}.png`)
  await (target ? target.screenshot({ path: f }) : page.screenshot({ path: f })); written.push(f)
}

const SURF = {
  async economics(page, name) {
    await showTab(page, 'economics'); await page.getByText('Total revenue').first().waitFor(); await page.waitForTimeout(1000)
    await shot(page, name)
    await page.getByText('Storage', { exact: true }).first().click().catch(() => {}); await page.waitForTimeout(400)
    await shot(page, name.replace('economics-', 'economics-storage-'))
  },
  async loadflow(page, name) {
    await showTab(page, 'loadflow'); await page.getByText(/Carrier KPIs/).first().waitFor(); await page.waitForTimeout(800)
    await page.getByText(/Carrier KPIs/).first().scrollIntoViewIfNeeded(); await page.waitForTimeout(400)
    await shot(page, name)
  },
  async dispatch(page, name, variant) {
    // Real network (template), mocked solved series sized to its real generators / storage / loads.
    const net = await page.evaluate(async () => {
      const { networkApi } = await import('/src/api/network.ts')
      const [g, st, l, sn] = await Promise.all([networkApi.getGenerators(), networkApi.getStorageUnits(), networkApi.getLoads(), networkApi.getSnapshots()])
      return { g: g.map(x => ({ name: x.name, carrier: x.carrier })), st: st.map(x => ({ name: x.name, carrier: x.carrier, bus: x.bus })), l: l.map(x => x.name), idx: (sn.snapshots ?? sn).slice(0, 48).map(x => (typeof x === 'string' ? x : x.snapshot ?? x.name ?? String(x))) }
    })
    console.log(name, 'network', JSON.stringify({ g: net.g.length, st: net.st.length, l: net.l.length, idx: net.idx.length, carriers: [...new Set(net.g.concat(net.st).map(x => x.carrier))] }))
    const ts = (cols, f) => ({ index: net.idx, columns: cols, data: net.idx.map((_, i) => cols.map((c, j) => f(i, j))) })
    const ctx = page.context(); const jr = (path, body) => ctx.route(u => u.pathname === path, r => json(r, body))
    await jr('/api/results/generators', ts(net.g.map(x => x.name), (i, j) => 40 + 10 * ((i + j) % 5)))
    await jr('/api/results/curtailment', ts(net.g.map(x => x.name), () => 0))
    await jr('/api/results/storage', ts(net.st.map(x => x.name), (i) => 10 + (i % 7)))
    await jr('/api/results/storage_dispatch', ts(net.st.map(x => x.name), (i) => ((i % 6) - 3) * 2))
    await jr('/api/results/loads', ts(net.l, () => 60))
    const carriers = [...new Set(net.g.concat(net.st).map(x => x.carrier))]
    const stCar = new Set(net.st.map(x => x.carrier))
    const by = {}
    for (const c of carriers) {
      const b = JSON.parse(JSON.stringify(lpCarrier.gas)); const hold = stCar.has(c)
      if (variant === 'after') { b.revenue_meur = null; b.storage_charge_cost_meur = null; if (hold) { b.opex_meur = null; b.lcoe_eur_per_mwh = null } }
      else { b.revenue_meur = pv(0); b.storage_charge_cost_meur = pv(0) }
      by[c] = b
    }
    await ctx.route(u => u.pathname === '/api/results/economics_by_carrier', r => json(r, { available: true, by_carrier: by, ...(variant === 'after' ? { unavailable_reason: 'mip_duals_not_marginal' } : {}) }))
    // The template has one bus carrier, and Dispatch then draws no per-carrier KPI panel: tag a bus without
    // storage `heat` so the AC panel (gas, solar, battery) renders beside a heat one.
    await ctx.route(u => u.pathname === '/api/network/buses', async r => {
      const resp = await r.fetch(); const buses = await resp.json()
      const free = buses.filter(b => !net.st.some(x => x.bus === b.name)); if (free.length && buses.length > 1) free[free.length - 1].carrier = 'heat'
      await r.fulfill({ response: resp, json: buses })
    })
    await page.reload(); await page.waitForLoadState('networkidle'); await page.waitForTimeout(1500)
    await showTab(page, 'dispatch'); await page.waitForTimeout(3000)
    if (process.env.DEBUG) console.log(await page.evaluate(() => [...document.querySelectorAll('h3,h4')].map(h => h.tagName + ':' + h.textContent.trim().slice(0, 50)).join(' | ')))
    console.log(name, 'opex count', await page.getByText('OPEX (total)').count(), 'econ-empty', await page.getByText('No economic data for this carrier').count())
    const kp = page.getByText('Storage charge cost', { exact: true }).first()
    await kp.waitFor(); await kp.scrollIntoViewIfNeeded(); await page.waitForTimeout(600)
    await shot(page, name)
  },
  async compare(page, name) {
    await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('compare') })
    await page.getByText('Per-asset levelised cost').first().waitFor(); await page.waitForTimeout(1000)
    await shot(page, name)
    await page.getByText('Per-asset levelised cost').first().scrollIntoViewIfNeeded(); await page.waitForTimeout(400)
    await shot(page, name.replace('compare-', 'compare-lcoh-'))
  },
}

const browser = await chromium.launch()
try {
  for (const variant of (process.env.VARIANTS || 'before,after').split(',')) {
    const base = `http://127.0.0.1:${PORTS[variant]}`
    const auth = await login(browser, base)
    for (const scheme of (process.env.SCHEMES || 'light,dark').split(',')) for (const mode of ['guided', 'expert']) for (const s of SURFACES) {
      const name = `${s}-${variant}-${mode}-${scheme}`
      if (mode === 'guided' && s !== 'compare') continue // expertOnly tabs
      const { ctx, page } = await open(browser, auth, variant, scheme, mode)
      try { await land(page, base); await SURF[s](page, name, variant) }
      catch (e) { problems.push(`${name}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${name}.png`) }) }
      await ctx.close()
    }
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
