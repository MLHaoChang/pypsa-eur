// Template-e2e 22 S1 shots: the Improve card's "Open in Expert" and each
// Expert place it opens, filled in. Real API; only the study and its review
// are mocked (the review's findings are the engine's own actions, from
// pypsa-gui/frontend/src/pages/hubDesign/__fixtures__/ehReviewActions.json).
// Run: node .scratch/template-e2e/shots/22-s1/shots.mjs (app on :8000/:5173).
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || HERE
const PROJECT = 'te22-improve'
const TEMPLATE = 'eh_datacenter'
const problems = []
const written = []

const fx = JSON.parse(fs.readFileSync(path.join(HERE, '../../../../pypsa-gui/frontend/src/pages/hubDesign/__fixtures__/ehReviewActions.json'), 'utf8'))
const pick = c => fx.findings.find(f => f.case === c)
// The rerun copies this project's own request; the fixture's record is a
// weak_flexible one, so drop its site type and weak-only fields here.
const ownArgs = a => {
  if (a.tool !== 'run_eh_study') return a
  const { archetype: _a, dsr_buses: _d, ...args } = a.args
  const po = { ...(args.pack_overrides ?? {}) }; delete po.import_p_nom_mw
  return { ...a, args: { ...args, pack_overrides: po } }
}
const finding = (c, over = {}) => { const f = pick(c); return { ...f, evidence: { note: 'from the study' }, recommendation: 'See the study.', actions: f.actions.map(ownArgs), ...over } }
const FINDINGS = [
  finding('certification_fail_energy_limited'),
  finding('fmea_dominant_mode'),
  finding('not_established_voll', { title: 'fmea_top: not established' }),
  { id: 'ens_target_missed', severity: 'high', title: 'ENS target missed: 1.2 ‱ vs 0.5 ‱', evidence: {}, recommendation: 'Raise candidates’ p_nom_max.', actions: [] },
]
// One finding whose rerun carries a setting the form cannot show (named on the card).
const withDtc = finding('dtc_critical_unserved')
withDtc.actions = withDtc.actions.map(a => ({ ...a, args: { ...a.args, dtc_config: { contingencies: ['grid_import'] } } }))
FINDINGS.push(withDtc)
const REVIEW = { status: 'ok', source: 'stored report', stale: false,
  summary: { verdict: 'fail', target_lole_h: 3, mc_lole_h_per_year: 8, cost_at_target_eur: 3.4e7 }, findings: FINDINGS }
const REPORT = { archetype: 'strong_grid', pack_hash: 'x', assumptions_hash: 'y', ens_cap_permyriad: 10, achieved_ens_permyriad: 9,
  mc_lole_h: 8, certified: false, cost_at_target_eur: 3.4e7, period_basis: 'year', completeness: { certification: 'ok' },
  sections: {}, pipeline: { stages: [], solves_consumed: 12, budget_solves: 20 } }

const browser = await chromium.launch()
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(`${BASE}/login`)
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  await login.waitForLoadState('networkidle')
  const made = await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) return 'exists'
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  console.log('project', made)
  const auth = await owner.storageState()
  await owner.close()

  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: scheme })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ project, scheme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', 'guided')
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', scheme)
    }, { project: PROJECT, scheme })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${scheme}: page error: ${e.message}`))
    await page.route(u => u.pathname === '/api/results/eh_study', r => r.request().method() === 'GET' ? r.fulfill({ json: { status: 'done', report: REPORT, error: null } }) : r.abort())
    await page.route(u => u.pathname === '/api/results/eh_reference_design', r => r.fulfill({ json: REPORT }))
    await page.route(u => u.pathname === '/api/results/eh_review', r => r.fulfill({ json: REVIEW }))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.waitForTimeout(2500)
    const shot = async (box, name) => {
      try {
        await box.scrollIntoViewIfNeeded()
        const f = path.join(OUT, `${name}-${scheme}.png`)
        await box.screenshot({ path: f }); written.push(f)
      } catch (e) { problems.push(`${scheme}/${name}: ${e.message.split('\n')[0]}`) }
    }
    const view = async name => { const f = path.join(OUT, `${name}-${scheme}.png`); await page.screenshot({ path: f }); written.push(f) }
    const toImprove = async () => {
      await page.getByTestId('hub-design-panel').waitFor({ timeout: 8000 }).catch(async () => {
        await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('hubDesign') })
        await page.getByTestId('hub-design-panel').waitFor()
      })
      await page.getByTestId('hub-rail-step-improve').click(); await page.waitForTimeout(1500)
      await page.getByTestId('hub-improve-list').waitFor()
    }
    const back = async () => {
      const pill = page.getByRole('button', { name: /^Back to/ }).first()
      await pill.click(); await page.waitForTimeout(1500)
    }
    try {
      await toImprove()
      await shot(page.getByTestId('hub-improve-list'), '1-improve-card')
      // Study re-run: Results › Adequacy, form filled in.
      await page.getByTestId('hub-improve-open-fmea_dominant_mode').click(); await page.waitForTimeout(2500)
      await page.getByTestId('eh-filled-from-improve').scrollIntoViewIfNeeded()
      await view('2-expert-adequacy-filled')
      await back(); await page.getByTestId('hub-improve-list').waitFor().catch(toImprove)
      // VOLL: Solver settings, unsaved.
      await page.getByTestId('hub-improve-open-not_established_fmea_top').click(); await page.waitForTimeout(2500)
      await page.getByTestId('solver-voll-unsaved').waitFor()
      await view('3-expert-voll-unsaved')
      await page.getByRole('button', { name: 'Revert', exact: true }).last().click(); await page.waitForTimeout(500)
      await back(); await page.getByTestId('hub-improve-list').waitFor().catch(toImprove)
      // Goal: Solver settings › Reliability goal, unsaved.
      await page.getByTestId('hub-improve-open-certification_fail').click(); await page.waitForTimeout(2500)
      await page.getByTestId('solver-goal-unsaved').waitFor()
      await view('4-expert-goal-unsaved')
      await page.getByRole('button', { name: 'Revert', exact: true }).last().click(); await page.waitForTimeout(500)
      await back(); await page.getByTestId('hub-improve-list').waitFor().catch(toImprove)
      // No action: Capacity bounds.
      await page.getByTestId('hub-improve-open-ens_target_missed').click(); await page.waitForTimeout(2500)
      await view('5-expert-capacity-bounds')
    } catch (e) { problems.push(`${scheme}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${scheme}.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
