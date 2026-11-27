// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Mocked API for scripts/smoke-result-navigation.mjs.
 *
 * result-navigation-fixture.json holds GET answers recorded once from the real
 * backend (composer-fixture.py: the oj-grid network for `single`, its two-period
 * lc16c variant for what differs in `multi`), solved with HiGHS; scratch paths
 * and tokens were scrubbed. The hand-written routes below are auth, chat, the
 * process view (so Grid risk and Products show) and the retained run.
 */
import fs from 'node:fs'

export const PROJECT = 'Navigation demo'
export const RUN_ID = 'fg-0a1b2c3d'
const P = `/api/projects/${encodeURIComponent(PROJECT)}`
const CE = `/api/campus-electrical/${encodeURIComponent(PROJECT)}`

const RECORDED = JSON.parse(fs.readFileSync(new URL('./result-navigation-fixture.json', import.meta.url), 'utf8'))
const GRID_RISK = { available: true, project_id: 'p', omitted: null, periods: ['_'], unit: 't/yr', currency: 'EUR',
  eur_inputs: [], overlap_rule: 'upper_bound', overlap_label: 'upper bound (lines may overlap)',
  lines: [{ key: 'ride_through', label: 'Ride-through (voltage dips)', status: 'ok', missing: [], missing_inputs: [], flags: [],
    basis: "the campus study's ride-through total as it wrote it", source_files: ['campus_dip_trips.csv'],
    stamp: { run_at: '2026-11-20T10:05:00+00:00' }, stale: false,
    rows: [{ key: 'Electrolyser', label: 'Electrolyser', flags: [], by_period: { _: { t_per_year: 0.4, eur_per_year: 1200, eur_flag: null } } }],
    by_period: { _: { t_per_year: 0.4, eur_per_year: 1200, eur_flag: null } } }],
  total: { label: 'Total', overlap_rule: 'upper_bound', incomplete: false, lower_bound_lines: [],
    by_period: { _: { t_per_year: 0.4, eur_per_year: 1200, eur_flag: null } } } }
const amount = (value, extra = {}) => ({ value, unit: 'MWh', t: value == null ? null : value * 0.03, mwh: value, flag: null, ...extra })

/** A recorded GET body; a route missing from the fixture fails the run here. */
const rec = (kind, path) => {
  const hit = RECORDED[kind]?.[path] ?? RECORDED.single[path]
  if (!hit || hit[0] !== 200) throw new Error(`result-navigation-fixture.json has no 200 answer for ${kind} ${path}`)
  return hit[1]
}
/** A part of a multi-part bound view: null where its route had nothing (204), as the backend reads it. */
const part = (kind, path) => {
  const hit = RECORDED[kind]?.[path] ?? RECORDED.single[path]
  return hit?.[0] === 204 ? null : rec(kind, path)
}
const parts = (kind, names) => Object.fromEntries(names.map(n => [n, part(kind, `/api/results/${n}`)]))
const bound = (kind, view, data) => [200, { status: 'ok', data,
  binding: { version: 1, project_id: 'p', project_name: PROJECT, scope: 'current', view, input_sha256: 'a'.repeat(64), data_sha256: 'b'.repeat(64) } }]

/** Hand-written routes: exact pathname → (kind, url) → [status, body]. */
const HAND = {
  '/api/health': () => [200, { status: 'ok', auth_enabled: true }],
  '/api/auth/me': () => [200, { id: 'user-1', email: 'owner@example.com', status: 'active', is_super_admin: false, org_id: 'org-1', role: 'admin' }],
  '/api/chat/profiles': () => [200, { active_profile_id: 'fixture',
    profiles: [{ id: 'fixture', label: 'Fixture model', wire: 'openai', chat_ready: true }] }],
  '/api/chat/health': () => [200, { ok: true, chat_ready: true, active_profile: { id: 'fixture', label: 'Fixture model', wire: 'openai' } }],
  '/api/chat/history': () => [200, { turns: [], last_session_id: null, bound_project: PROJECT, history_gap: 0, pending_turn: null,
    workflow: null, goal: null, bound_profile_id: null }],
  '/api/chat/workflows': () => [200, { workflows: [] }],
  '/api/chat/reliability/capabilities': () => [200, { version: 1, mode: 'disabled', profiles: {} }],
  '/api/chat/settings/llm': () => [204, null],
  '/api/chat/settings/api-key': () => [200, { configured: false, source: null, hint: null, overridden_by_environment: false, redactable: null }],
  '/api/dictation/config': () => [200, { available: false, languages: ['en'], max_bytes: 10485760, max_recording_seconds: 120 }],
  // One process unit on a hydrogen bus: Grid risk and Products show (tabsFor flags processUnits, products).
  '/api/results/process_view': () => [200, { available: true,
    process_units: [{ name: 'Electrolyser', product_bus: 'H2', running_hours: 20, modelled_hours: 24, weighted_hours: 24, flag: null }],
    products: [{ bus: 'H2', carrier: 'hydrogen', unit: 'MWh', t_per_unit: 0.03,
      production: amount(120), delivery: amount(118), buy_in: amount(0, { eur: 0, carriers: [] }),
      lost: amount(2, { eur: 300, carriers: [] }), inventory: [] }] }],
  // The retained run discuss_project_run opens (RunBoundView, useRunStartedAt).
  '/api/results/run-view': (_kind, url) => [200, { status: 'ok',
    binding: { version: 1, project_id: 'p', project_name: PROJECT, scope: 'historical', run_id: url.searchParams.get('run_id'),
      section: url.searchParams.get('section'), baseline_run_id: null, data_sha256: 'c'.repeat(64) },
    data: { run_id: url.searchParams.get('run_id'), status: 'completed', started_at: '2026-11-20T10:00:00Z', fingerprint: 'd'.repeat(12),
      section: null, metrics: { objective: { value: 77617, unit: 'EUR' } }, sections: [{ name: 'results_summary', status: 'kept', available: true }] } }],
  // The view answers for the window asked, as the backend does (BranchLoading checks the binding).
  '/api/results/loadflow-view': (kind, url) => {
    const [status, body] = RECORDED[kind]['/api/results/loadflow-view']
    const from = Number(url.searchParams.get('window_from') ?? body.window.from)
    const to = Number(url.searchParams.get('window_to') ?? body.window.to)
    return [status, { ...body, window: { ...body.window, from, to, snapshots: to - from + 1 },
      binding: { ...body.binding, window_from: from, window_to: to } }]
  },
  // A5.2 S3b-1's bound views: the parts recorded from their own routes, read together.
  '/api/results/displayed/capex': kind => bound(kind, 'capex', {
    cost_breakdown: rec(kind, '/api/results/cost_breakdown'), economics_by_carrier: rec(kind, '/api/results/economics_by_carrier') }),
  '/api/results/displayed/prices': kind => bound(kind, 'prices', {
    prices: rec(kind, '/api/results/prices'), price_drivers: rec(kind, '/api/results/price_drivers') }),
  // A5.2 S3b-2's bound views: Dispatch's eleven parts and the multi-period Overview's six.
  '/api/results/displayed/dispatch': kind => bound(kind, 'dispatch', {
    ...parts(kind, ['cost_breakdown', 'generators', 'curtailment', 'storage', 'storage_dispatch', 'loads', 'links', 'lost_load', 'economics_by_carrier']),
    store_dispatch: part(kind, '/api/results/store_dispatch'), store_energy: part(kind, '/api/results/store_energy') }),
  '/api/results/displayed/overview': kind => bound(kind, 'overview',
    parts(kind, ['generators', 'storage_dispatch', 'loads', 'curtailment', 'lost_load', 'cost_breakdown'])),
  '/api/results/displayed/storage': kind => bound(kind, 'storage', rec(kind, '/api/results/storage_dispatch')),
  '/api/results/displayed/grid_risk': () => [200, { status: 'ok', data: GRID_RISK,
    binding: { version: 1, project_id: 'p', project_name: PROJECT, scope: 'current', view: 'grid_risk', input_sha256: 'a'.repeat(64), data_sha256: 'b'.repeat(64) } }],
}

/** Each result tab's own data routes, read from its component (Results.tsx renderBody). The
 * journey fails when one of these is served by the catch-all instead of a mock above. */
export const TAB_ROUTES = {
  summary: ['/api/results/statistics', '/api/results/cost_breakdown', '/api/results/emissions', '/api/results/lost_load',
    '/api/results/adequacy', '/api/results/reserve_margin', '/api/results/process_view', '/api/results/headline_kpis'],
  overview: ['/api/results/displayed/overview'],
  capex: ['/api/results/displayed/capex', '/api/simulation/asset_costs', '/api/network/vintage_results'],
  dispatch: ['/api/results/displayed/dispatch'],
  loadflow: ['/api/results/loadflow-view', '/api/results/lines', '/api/results/transformers', '/api/results/prices',
    '/api/results/line_duals', '/api/results/losses', '/api/results/unit_commitment', '/api/results/carrier_kpis'],
  grid_code: [`/api/gridspine/${encodeURIComponent(PROJECT)}/status`, CE],
  electrical_design: [`/api/gridspine/${encodeURIComponent(PROJECT)}/status`],
  island: [`${CE}/island`, `${CE}/island/report`, CE],
  grid_forming: [`${CE}/island/gfm-settings`],
  flows: ['/api/results/displayed/flows'],
  loadshape: ['/api/results/displayed/loadshape'],
  prices: ['/api/results/displayed/prices', '/api/results/emissions'],
  economics: ['/api/results/asset_economics', '/api/results/lcoh'],
  emissions: ['/api/results/displayed/emissions'],
  curtailment: ['/api/results/displayed/curtailment', '/api/results/curtailment/by_cause'],
  lostload: ['/api/results/lost_load', '/api/results/adequacy'],
  adequacy: ['/api/results/adequacy', '/api/results/copt'],
  storage: ['/api/results/displayed/storage', '/api/results/reserves'],
  grid_risk: ['/api/results/displayed/grid_risk'],
  products: ['/api/results/process_view', '/api/results/lcoh'],
  fmea: ['/api/results/fmea_modes', `${P}/worksheet`, `${P}/stress_scenarios`],
  investment: ['/api/simulation/commercial/poc', '/api/results/billing', '/api/results/value_flows', '/api/results/investment_case'],
  asset: ['/api/results/asset/assets', '/api/results/asset/Bus/B1'],
  // Not a tab: the retained run discuss_project_run opens.
  run: ['/api/results/run-view'],
}

/** Each tab's own root (G10): the element its component renders once it has read its data. */
export const TAB_ROOTS = {
  summary: 'results-summary', grid_code: 'grid-code-tab', electrical_design: 'electrical-design-tab',
  island: 'island-tab', grid_forming: 'grid-forming-tab', flows: 'ef-view', loadshape: 'ls-view',
  products: 'products-tab', investment: 'investment-tab',
}
export const rootTestId = tab => TAB_ROOTS[tab] ?? `results-view-${tab}`

/** The explicit answer for a GET, or undefined (the catch-all serves it). */
export function mockFor(url, kind) {
  if (HAND[url.pathname]) return HAND[url.pathname](kind, url)
  return RECORDED[kind]?.[url.pathname] ?? RECORDED.single[url.pathname]
}
