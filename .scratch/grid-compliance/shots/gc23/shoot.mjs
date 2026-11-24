// GC-23 shots: the PSS/E case section, 5 cases x Guided/Expert x light/dark (also mocks /psse and its download). /api/campus-electrical/<project> is mocked
// (an invested study: switchgear PASS on the bought 31.5 kA, pcc_reactive PASS with no limit); the rest is the
// seeded backend (.scratch/grid-compliance/shots/setup.py) on :8010 and Vite on :5180 (see ../run.sh).
//   NODE_PATH=$(npm root -g) PWFILE=<app-data>/dev-admin-password OUT=<dir> node shoot.mjs
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE ?? 'http://127.0.0.1:5180'
const PROJECT = process.env.GC_SHOTS_PROJECT ?? 'Harbour Hub'
const OUT = process.env.OUT ?? '.'
const pw = fs.readFileSync(process.env.PWFILE, 'utf8').trim()
fs.mkdirSync(OUT, { recursive: true })

const base = (check, over) => ({
  check, status_as_is: 'pass', status_with_measures: 'pass', value: 1, limit: null, unit: '',
  worst_period: 2030, worst_hour: 158, clause: 'c', source: 'code', detail: 'd', ...over,
})
const inv = (row, over) => ({
  ...row, value_with_measures: row.value, worst_period_with_measures: 2030, worst_hour_with_measures: 158,
  detail_with_measures: row.detail, ...over,
})
const sw = base('switchgear', { status_as_is: 'not_rated', value: 25.0, unit: 'kA', clause: 'bus rating', source: 'library', detail: 'no rated bus' })
const qr = base('pcc_reactive', { value: 4.2, unit: 'Mvar', clause: 'study connection agreement', source: 'assumed', detail: 'no limit given' })
const pv = base('pcc_voltage', { value: 1.0, limit: 0.9, unit: 'pu', clause: 'DCC Annex II', detail: 'within the band' })

const state = {
  campus_yaml: 'campus:\n  pcc: {bus: GRID}\n', skipped: [],
  profiles: { eu_rfg_dcc_ce: 'EU RfG (2016/631) and DCC (2016/1388), Continental Europe' },
  settings: { k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: true,
              pcc_switchgear_by_operator: false, pcc_tap_control: 'campus' },
  stale: false, hub_cost: null, hub_cost_reason: 'no study has run yet',
  results: {
    selection: [{ period: 2030, hour: 158, reasons: ['max_consumption_mw'] }],
    transformers: [], compensation: [], short_circuit: [],
    compliance: [sw, qr, pv],
    requirement: { q_limit_mvar: 13.15, clause: 'study connection agreement', source: 'assumed', profile: 'eu_rfg_dcc_ce',
                   pf: 0.95, p_ref_mw: 40, p_ref_from: 'peak |import| over every hour' },
    investment: [], cost: [], history: [], unresolved: [], scope: null,
    compliance_invested: [
      inv(sw, { limit_with_measures: 31.5, detail_with_measures: 'every rated bus within its rating (worst bus PCC: 31.5 kA, source: library)' }),
      inv(qr, { detail_with_measures: 'reactive exchange recorded' }),
      inv(pv),
    ],
  },
}


const bundle = (hour) => ({ name: `p2030_h${hour}`, period: 2030, hour, files: [{ name: 'case.raw', size: 1200 }], size: 2048 })
const CASES = {
  bundles: { has_run: true, stale: false, run_at: '2026-11-20T10:00:00Z', bundles: [bundle(12), bundle(87)], skipped: null, error: null },
  stale: { has_run: true, stale: true, run_at: '2026-11-20T10:00:00Z', bundles: [bundle(12), bundle(87)], skipped: null, error: null },
  'no-run': { has_run: false, stale: false, run_at: null, bundles: [], skipped: null, error: null },
  'no-island-hours': { has_run: true, stale: false, run_at: '2026-11-20T10:00:00Z', bundles: [], skipped: 'no island critical hours in this run', error: null },
  'write-error': { has_run: true, stale: false, run_at: '2026-11-20T10:00:00Z', bundles: [], skipped: null, error: 'disk full' },
}
const texts = []
const b = await chromium.launch()
try {
  for (const theme of ['light', 'dark']) for (const [cname, psse] of Object.entries(CASES)) {
    const ctx = await b.newContext({ viewport: { width: 1440, height: 1800 }, deviceScaleFactor: 1 })
    const p = await ctx.newPage()
    await p.route(/\/api\/campus-electrical\/[^/?]+(\?.*)?$/, r =>
      r.request().method() === 'GET' ? r.fulfill({ json: state }) : r.continue())
    await p.route(/\/api\/campus-electrical\/[^/?]+\/psse(\?.*)?$/, r => r.fulfill({ json: psse }))
    await p.route(/\/api\/campus-electrical\/[^/?]+\/psse\/[^/]+\/download(\?.*)?$/, r =>
      r.fulfill({ status: 200, body: Buffer.from('PK\u0003\u0004'), contentType: 'application/zip', headers: { 'X-Run-Stale': String(psse.stale) } }))
    await p.route(/\/api\/projects\/[^/?]+\/flow\/scope(\?.*)?$/, async r => {
      if (r.request().method() !== 'GET') return r.continue()
      const res = await r.fetch()
      const j = await res.json()
      for (const s of [j.scope, j.preset]) {
        if (!s.groups.includes('grid')) s.groups.push('grid')
        s.options.grid = [...new Set([...(s.options.grid ?? []), 'grid_code'])]
      }
      return r.fulfill({ response: res, json: j })
    })
    await p.goto(`${BASE}/`)
    await p.getByLabel('Email').fill('admin@example.com')
    await p.getByLabel('Password').fill(pw)
    await p.getByRole('button', { name: 'Sign in' }).click()
    await p.getByRole('link', { name: new RegExp(PROJECT) }).first().click()
    await p.waitForTimeout(1500)
    const toggle = p.getByRole('button', { name: /^Switch to (light|dark) theme$/ }).first()
    if ((await toggle.getAttribute('aria-label')) === `Switch to ${theme} theme`) await toggle.click()
    const ui = (fn, arg) => p.evaluate(async ([f, a]) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      return new Function('s', 'a', f)(useUIStore.getState(), a)
    }, [fn, arg])
    for (const mode of ['guided', 'expert']) {
      await ui('s.setUiMode(a, true)', mode)
      await ui("s.setSlidePanel('results'); s.requestResultsTab('grid_code')")
      const section = p.locator('section', { has: p.getByText('PSS/E case', { exact: true }) }).last()
      await section.waitFor({ timeout: 30000 })
      await p.waitForTimeout(1000)
      await section.scrollIntoViewIfNeeded()
      const f = path.join(OUT, `${mode}-${theme}-${cname}.png`)
      await section.screenshot({ path: f, animations: 'disabled' })
      console.log(f)
      const t = (await section.innerText()).replace(/\s+/g, ' ')
      const th = await p.evaluate(() => document.documentElement.className + '|' + document.documentElement.dataset.theme)
      console.log('TEXT', mode, theme, cname, th, '::', t)
    }
    await ctx.close()
  }
} finally {
  await b.close()
}
