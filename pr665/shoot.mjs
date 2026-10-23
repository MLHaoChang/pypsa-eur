// Screenshots for PR #665. Run: node shoot.mjs (vite dev server on :5199 serving harness.html).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = new URL('.', import.meta.url).pathname
const row = o => ({ need: 'transformer GRID_IMPORT', library_id: 'TR_110_20_63', kind: 'transformer', units: 1, length_km: null,
  invest_period: 2030, capex_eur: 2150000, opex_eur_per_a: 32250, annualised_eur_per_a: 193519.65, existing: false,
  status: 'chosen', reason: null, ...o })
const results = { selection: [{ period: 2030, hour: 158, reasons: ['max_consumption_mw'] }], transformers: [], compensation: [],
  short_circuit: [], compliance: [],
  requirement: { q_limit_mvar: 13.15, clause: 'agreement', source: 'assumed', profile: 'eu_rfg_dcc_ce', pf: 0.95, p_ref_mw: 40, p_ref_from: 'peak' },
  investment: [row({}), row({ need: 'switchgear GRID', library_id: 'SG_110_31p5', kind: 'switchgear', units: 2, capex_eur: 800000, annualised_eur_per_a: 68007.31 })],
  cost: [{ period: 2030, capex_eur: 2950000, annualised_eur_per_a: 261526.96 }],
  compliance_invested: [], history: [], unresolved: [], scope: { pcc_switchgear: 'campus' } }
const milp = { status: null, stale: false, results: {
  investment: [row({ library_id: 'TR_110_20_50', capex_eur: 1800000, annualised_eur_per_a: 150000 })],
  cost: [{ period: 2030, capex_eur: 1800000, annualised_eur_per_a: 150000 }], compliance_invested: [], history: [],
  comparison: [{ need: 'transformer GRID_IMPORT', c8_choice: '1 x TR_110_20_63', milp_choice: '1 x TR_110_20_50',
    c8_annualised_eur_per_a: 193519.65, milp_annualised_eur_per_a: 150000 }],
  summary: { method: 'milp', fallback: false, reason: '', stop: 'converged', c8_cost: 261526.96, milp_cost: 150000, c8_feasible: true, iterations: 1 },
  fallback: null, stop: 'converged' } }
const state = { campus_yaml: 'campus:\n  pcc: {bus: GRID}\n', skipped: [], profiles: { eu_rfg_dcc_ce: 'EU RfG' }, settings: null,
  results, stale: false, hub_cost: { basis: 'per_period', per_period: { '2030': 1000000 }, total: 10000000 }, hub_cost_reason: null,
  owner_assets_count: 1, owner_assets_method: 'milp', owner_assets_unresolved: 0, milp }
const LIB = 'discount_rate: {value: 0.07, source: assumed}\ntransformers:\n  - id: TR_110_20_63\n    capex_eur: {value: 2150000, source: literature}\n'
const routes = {
  '/api/campus-electrical/Demo/library': { yaml: LIB, is_default: true },
  '/api/campus-electrical/Demo/grid-codes': { shipped: {}, published: [], drafts: [], documents: [], extraction_available: false },
  '/api/campus-electrical/Demo/milp': null,
  '/api/campus-electrical/Demo': state,
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['expert', 'guided']) {
  const p = await (await browser.newContext({ viewport: { width: 1100, height: 900 } })).newPage()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    const hit = Object.keys(routes).sort((a, b) => b.length - a.length).find(k => path === k)
    console.log('api', path, hit ? 'mocked' : '404')
    return hit ? route.fulfill({ json: routes[hit] }) : route.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  await p.goto(`${BASE}?view=library&mode=${mode}`); await p.waitForTimeout(1500)
  const lib = p.getByText("The shipped library's costs are reference values")
  await lib.scrollIntoViewIfNeeded(); await p.waitForTimeout(300)
  await p.screenshot({ path: `${OUT}01-library-reference-values-${mode}.png` })
  await p.goto(`${BASE}?view=investment&mode=${mode}`); await p.waitForTimeout(1500)
  await p.getByText('Prices come from the asset library').scrollIntoViewIfNeeded()
  await p.screenshot({ path: `${OUT}02-investment-reference-values-${mode}.png` })
}
await browser.close()
