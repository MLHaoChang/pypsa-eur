// Screenshots for PR #671. Run: node shoot.mjs (vite dev server on :5199 serving harness.html).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'node:fs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = process.env.OUT
const golden = JSON.parse(fs.readFileSync('../backend/tests/fixtures/island_report_golden.json', 'utf8'))
const asset = (name, over = {}) => ({ name, kind: 'transformer', basis: 'lump', quantity: 1, overnight_cost: 1800000,
  build_year: 2030, source: 'campus_study', source_hash: '0123456789abcdef', money_year: 'real 2025 EUR',
  cod: '2030-01-01', provenance: 'assumed', illustrative: true, ...over })
const report = { case_id: 'investment_case', assumptions_hash: 'abcdef0123456789', packs: {}, project_irr_pre_tax: 0.085,
  project_irr_post_tax: null, npv_at_wacc: 1234567.8, lcoe_finance_consistent_eur_per_mwh: 55.5, ppa_price_for_target_irr_eur_per_mwh: null,
  min_dscr: 1.3, avg_dscr: 1.45, llcr: null, plcr: null, wacc_vs_discount_rate_consistent: null,
  completeness: { design: 'skipped', commercial: 'skipped', dispatch_modes: 'skipped', participants: 'ok', project: 'ok', debt: 'ok',
    tax: 'not_established', tax_equity: 'skipped', uncertainty: 'skipped', gates: 'not_established' },
  sections: { project: { status: 'ok', payload: { years: [2030, 2031, 2032], lifecycle_npv: -5000000, payback_years: 7.25,
    has_counterfactual: true, incremental_net: [0, 100, 110], counterfactual_net: [0, -900, -910],
    extra_assets: [asset('campus:TR_132_33_63#1'), asset('campus:CB_33_AL240#1', { kind: 'cable', basis: 'per_km', quantity: 2.5,
      overnight_cost: 550000, provenance: 'literature', illustrative: false })],
    campus_study: { study_method: 'least_cost', unresolved: [] },
    flags: ['campus_life_ends_unreplaced:campus:TR_132_33_63#1:2042:fixed', 'part_not_replaced:campus:TR_132_33_63#1:investment',
      'part_not_replaced:campus:CB_33_AL240#1:investment', 'part_not_replaced:bess:power', 'campus_renewal_after_horizon:campus:TR_132_33_63#1:2055',
      'load_shed_excluded:1.5'] } } },
  cashflow_lines: [] }
const LIB = 'discount_rate: {value: 0.07, source: assumed}\ntransformers:\n  - id: TR_110_20_63\n    capex_eur: {value: 2150000, source: literature}\n'
const state = { campus_yaml: 'campus:\n  pcc: {bus: GRID}\n', skipped: [], profiles: {}, settings: null, results: null, stale: false,
  hub_cost: null, hub_cost_reason: null, owner_assets_count: 1, owner_assets_method: 'milp', owner_assets_unresolved: 0, milp: null }
const routes = {
  '/api/results/investment_case': { status: 'done', report: { present: true, stale: false } },
  '/api/results/investment_case/report': report,
  '/api/__golden': golden,
  '/api/campus-electrical/Demo/library': { yaml: LIB, is_default: true },
  '/api/campus-electrical/Demo/grid-codes': { shipped: {}, published: [], drafts: [], documents: [], extraction_available: false },
  '/api/campus-electrical/Demo': state,
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['expert', 'guided']) {
  const p = await (await browser.newContext({ viewport: { width: 1100, height: 1000 } })).newPage()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    return path in routes ? route.fulfill({ json: routes[path] }) : route.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  await p.goto(`${BASE}?view=ic&mode=${mode}`); await p.waitForSelector('[data-testid=ic-campus]'); await p.waitForTimeout(500)
  await p.getByTestId('ic-campus').scrollIntoViewIfNeeded()
  await p.getByTestId('ic-campus').screenshot({ path: `${OUT}/01-ic-campus-equipment-${mode}.png` })
  await p.getByTestId('ic-counterfactual').screenshot({ path: `${OUT}/02-ic-project-flags-${mode}.png` })
  await p.goto(`${BASE}?view=library&mode=${mode}`); await p.waitForTimeout(1500)
  const lib = p.getByText("The shipped library's costs are reference values")
  await lib.scrollIntoViewIfNeeded(); await p.screenshot({ path: `${OUT}/03-source-literature-${mode}.png` })
  await p.goto(`${BASE}?view=island&mode=${mode}`); await p.waitForSelector('[data-testid=island-table-binding]'); await p.waitForTimeout(500)
  await p.getByTestId('island-table-binding').screenshot({ path: `${OUT}/04-island-limits-title-${mode}.png` })
  await p.getByTestId('island-table-costs').screenshot({ path: `${OUT}/05-island-costs-status-${mode}.png` })
}
await browser.close()
