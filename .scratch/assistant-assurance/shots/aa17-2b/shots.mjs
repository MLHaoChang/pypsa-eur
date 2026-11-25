// AA-17 2b before/after shots: a MIP solve's marginal prices are withheld ("not available")
// instead of 0. Real app (sign-in, projects, network) plus route mocks for the price payloads.
// Two Vite servers: BEFORE_PORT 5175 (master worktree), AFTER_PORT 5174 (c2c1afd0 worktree);
// one backend on :8000 started with CORS_ALLOWED_ORIGINS=http://127.0.0.1:5174,http://127.0.0.1:5175
// and XDG_DATA_HOME=<scratch> (the dev admin password is read from there by dev-credentials.mjs).
// Surfaces: `prices` = Results > Prices tab, `expertOnly: true` in Results.tsx TABS, so Expert only.
// `asset` / `assetprices` = Asset Detail drawer (uiStore.requestAssetDetail) for a Generator,
// categories economics / prices; Guided and Expert.
// Run: OUT=<dir> PW_FROM=<path to playwright> node shots.mjs [surface...]
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)(process.env.PW_FROM || 'playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = process.env.OUT || HERE
const PORTS = { before: 5175, after: 5174 }
const SURFACES = process.argv.slice(2).length ? process.argv.slice(2) : ['prices', 'asset', 'assetprices']
const PROJECT = 'aa17-2b'
const problems = [], written = []
const REASON = 'Not available. An integer (MIP) solve has no marginal prices.'  // MIP_PRICES_TEXT
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const CATS = [['summary', 'Summary'], ['capacity', 'Capacity'], ['dispatch', 'Dispatch'], ['storage', 'Storage'], ['loadflow', 'Load flow'], ['prices', 'Prices & duals'], ['economics', 'Economics'], ['emissions', 'Emissions']]
// [id, label, unit, kind, category, origin, needs prices]
const METRICS = [
  ['bus_marginal_price', 'Bus marginal price', 'EUR/MWh', 'series', 'prices', 'output', true],
  ['mu_upper', 'μ upper', 'EUR/MWh', 'series', 'prices', 'output', true],
  ['mu_lower', 'μ lower', 'EUR/MWh', 'series', 'prices', 'output', true],
  ['capture_price', 'Capture price', 'EUR/MWh', 'scalar', 'prices', 'derived', true],
  ['capture_rate', 'Capture rate', 'pu', 'scalar', 'prices', 'derived', true],
  ['binding_hours', 'Binding hours', 'h', 'scalar', 'prices', 'derived', true],
  ['revenue_eur', 'Revenue', 'EUR', 'scalar', 'economics', 'derived', true],
  ['vom_cost_eur', 'Variable O&M', 'EUR', 'scalar', 'economics', 'derived', false],
  ['fixed_cost_eur', 'Fixed cost', 'EUR', 'scalar', 'economics', 'derived', false],
  ['net_profit_eur', 'Net profit', 'EUR', 'scalar', 'economics', 'derived', true],
  ['lcoe_eur_per_mwh', 'LCOE', 'EUR/MWh', 'scalar', 'economics', 'derived', false],
]
const OTHER = { vom_cost_eur: 12500, fixed_cost_eur: 84000, lcoe_eur_per_mwh: 61.3 }

function assetResponse(variant, asset, category, idx) {
  const after = variant === 'after'
  const rows = METRICS.filter(m => m[4] === category).map(([id, label, unit, kind, , origin, needs]) => ({
    id, label, unit, kind, origin,
    ...(needs && after ? { status: 'blocked', reason: REASON } : { status: 'ok' }),
  }))
  const ok = rows.filter(r => r.status === 'ok')
  const scalars = {}, series = {}, columns = []
  for (const r of ok) {
    if (r.kind === 'scalar') scalars[r.id] = r.id in OTHER ? OTHER[r.id] : 0
    else { series[r.id] = idx.map(() => 0); columns.push({ id: r.id, label: r.label, unit: r.unit, metric_id: r.id, agg: null }) }
  }
  return {
    asset: { ...asset, params: { p_nom_opt: 100, marginal_cost: 40 } },
    solve: { source: 'lopf', objective: 123456, solve_time: 4.2, condition: 'optimal' },
    category, mode: 'chronological',
    categories: CATS.map(([id, label]) => ({ id, label, status: 'ok' })),
    metrics: rows, scalars, headline: [], reading_notes: [],
    index: columns.length ? idx : [], periods: null, pct_of_hours: null, columns, series,
  }
}

async function mock(ctx, variant, net) {
  const after = variant === 'after'
  await ctx.route(u => u.pathname === '/api/results/prices', r => json(r, after
    ? { status: 'unavailable', reason: 'mip_duals_not_marginal' }
    : {
      index: net.idx, columns: net.buses, data: net.idx.map(() => net.buses.map(() => 0)),
      data_adjusted: net.idx.map(() => net.buses.map(() => 0)), negative_hours: 0,
      source: 'fallback', fallback_per_snapshot: net.idx.map(() => 60),
      note: 'LP duals are zero — most likely either (a) solver skipped dual extraction, or (b) demand is served by ample low-cost capacity so an extra MW would cost 0. The fallback shows the highest-marginal-cost dispatching generator per snapshot.',
    }))
  await ctx.route(u => u.pathname === '/api/results/price_drivers', r => json(r, after
    ? { status: 'unavailable', reason: 'mip_duals_not_marginal' }
    : { threshold: 2000, total_above_threshold: 0, truncated: false, rows: [] }))
  await ctx.route(u => u.pathname === '/api/results/asset/assets', r => json(r, { assets: net.gens }))
  await ctx.route(u => /^\/api\/results\/asset\/Generator\/[^/]+$/.test(u.pathname), r => {
    const name = decodeURIComponent(new URL(r.request().url()).pathname.split('/').pop())
    const cat = new URL(r.request().url()).searchParams.get('category') || 'summary'
    const a = net.gens.find(g => g.name === name) || net.gens[0]
    return json(r, assetResponse(variant, a, cat, net.idx))
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
  const made = await p.evaluate(async (name) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(x => x.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate('eh_datacenter', name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, PROJECT)
  console.log(base, PROJECT, made)
  // A solved run state (UX 29 on #1994): the header and pills read solved, not "not run yet".
  // The price and asset routes are still mocked per variant in `mocks`.
  const solved = await p.evaluate(async (name) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const { simulationApi } = await import('/src/api/simulation.ts')
    await projectsApi.activate(name).catch(() => null)  // activated beforehand when the browser call is refused
    let st = await simulationApi.getStatus()
    if (st.dispatch !== 'fresh') {
      await simulationApi.run()
      for (let i = 0; i < 240; i++) {
        await new Promise(r => setTimeout(r, 1000))
        st = await simulationApi.getStatus()
        if (!st.running && st.dispatch === 'fresh') break
      }
    }
    return `${st.status}/${st.dispatch}`
  }, PROJECT)
  console.log(base, PROJECT, 'run state:', solved)
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
  }, { project: PROJECT, scheme, mode })
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
async function netInfo(page) {
  return page.evaluate(async () => {
    const { networkApi } = await import('/src/api/network.ts')
    const [g, b, sn] = await Promise.all([networkApi.getGenerators(), networkApi.getBuses(), networkApi.getSnapshots()])
    return {
      gens: g.map(x => ({ class: 'Generator', name: x.name, carrier: x.carrier, bus: x.bus })),
      buses: b.map(x => x.name),
      idx: (sn.snapshots ?? sn).slice(0, 24).map(x => (typeof x === 'string' ? x : x.snapshot ?? x.name ?? String(x))),
    }
  })
}
async function shot(page, name) { const f = path.join(OUT, `${name}.png`); await page.screenshot({ path: f }); written.push(f) }

const SURF = {
  async prices(page, name) {
    await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('prices') })
    await page.waitForTimeout(3000)
    console.log(name, 'text:', (await page.locator('body').innerText()).match(/Marginal prices: not available[^\n]*/)?.[0] ?? '(no withheld sentence)',
      '| svg charts:', await page.locator('svg.recharts-surface').count())
    await shot(page, name)
  },
  async asset(page, name, _v, category = 'economics') {
    const net = page.__net
    await page.evaluate(async ({ n, category }) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().requestAssetDetail({ componentClass: 'Generator', name: n, category })
    }, { n: net.gens[0].name, category })
    await page.getByTestId('asset-drawer').waitFor(); await page.waitForTimeout(2500)
    await shot(page, name)
  },
  async assetprices(page, name, v) { return SURF.asset(page, name, v, 'prices') },
}

const browser = await chromium.launch()
try {
  for (const variant of (process.env.VARIANTS || 'before,after').split(',')) {
    const base = `http://127.0.0.1:${PORTS[variant]}`
    const auth = await login(browser, base)
    for (const scheme of (process.env.SCHEMES || 'light,dark').split(',')) for (const mode of ['guided', 'expert']) for (const s of SURFACES) {
      const name = `${s}-${variant}-${mode}-${scheme}`
      if (mode === 'guided' && s === 'prices') continue // expertOnly tab
      const { ctx, page } = await open(browser, auth, variant, scheme, mode)
      try {
        await land(page, base)  // resumes the project, so the backend has its network loaded
        page.__net = await netInfo(page)
        await mock(ctx, variant, page.__net)
        await SURF[s](page, name, variant)
      } catch (e) { problems.push(`${name}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${name}.png`) }) }
      await ctx.close()
    }
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
