// Template-e2e 22 S2b shots. Guided: the Results card's Investment tile with its
// run panel, before a run (real), refused (real: the data-centre template is not
// solved, so the start is refused with not_solved) and could not finish (the
// stored study is mocked as an error with no reason). Expert: the IPP template's
// Lifecycle-cost NPV row with its note (the report is mocked: no counterfactual,
// lifecycle-cost NPV equal to the equity NPV, as GK A found on the IPP).
// Run: OUT=<dir> node .scratch/template-e2e/shots/22-s2b/shots.mjs (app on :8000/:5173).
// The PNGs live on claude/campus-ic-02-05-shots under pr1886/ (UX ruling v), not in the PR.
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || '/tmp/claude-0/-home-user-pypsa-eur/f77ac539-8b2d-5d27-8ebc-cd919ce3a922/scratchpad/shots-1886'
const DC = { project: 'te22-investment', template: 'eh_datacenter' }
const IPP = { project: 'te22-ipp', template: 'eh_ipp_hybrid' }
// One predicate for the study route, so unroute() matches it (Playwright compares by identity).
const IC = u => u.pathname === '/api/results/investment_case'
const problems = []
const written = []

const IPP_REPORT = {
  npv_at_wacc: 18_400_000, project_irr_post_tax: 0.091,
  sections: { project: { status: 'ok', payload: {
    has_counterfactual: false, counterfactual: { present: false },
    project_post_tax_npv: 18_400_000, equity_post_tax_npv: 9_730_000, lifecycle_npv: 9_730_000,
    equity_post_tax_irr: 0.124, project_pre_tax_irr: 0.104,
  } } },
}

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
  for (const p of [DC, IPP]) {
    const made = await login.evaluate(async ({ tid, name }) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(x => x.name === name)) return 'exists'
      try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
    }, { tid: p.template, name: p.project })
    console.log(p.project, made)
  }
  const auth = await owner.storageState()
  await owner.close()

  async function open(scheme, { project, mode }) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: scheme })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ project, scheme, mode }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', scheme)
    }, { project, scheme, mode })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${scheme}: page error: ${e.message}`))
    return { ctx, page }
  }
  async function land(page) {
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.waitForTimeout(2500)
    await page.evaluate(() => { for (const n of document.querySelectorAll('div[style*="z-index: 9999"]')) n.style.visibility = 'hidden' })
  }
  const shot = async (page, name, scheme, target) => {
    const f = path.join(OUT, `${name}-${scheme}.png`)
    await (target ? target.screenshot({ path: f }) : page.screenshot({ path: f })); written.push(f)
  }
  async function guidedResults(page) {
    await page.evaluate(async () => {
      const { useHubDesignStore } = await import('/src/pages/hubDesign/hubDesignStore.ts')
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().setSlidePanel('hubDesign')
      useHubDesignStore.getState().setStep('results', { user: true })
    })
    // Before the hub study has results the Investment tile is not headlined: it carries the panel.
    await page.getByTestId('hub-chapter-investment').scrollIntoViewIfNeeded()
    await page.getByTestId('ic-run').waitFor(); await page.waitForTimeout(1200)
  }

  for (const scheme of ['light', 'dark']) {
    // Guided, data-centre template.
    let { ctx, page } = await open(scheme, { project: DC.project, mode: 'guided' })
    try {
      await land(page); await guidedResults(page)
      const headline = page.getByTestId('hub-chapter-investment')
      await shot(page, 'guided-1-before-run', scheme, headline)
      await page.getByTestId('ic-run').click()
      await page.getByTestId('ic-need-solve').waitFor(); await page.waitForTimeout(800)
      await shot(page, 'guided-2-refused-needs', scheme, headline)
      await page.route(IC, r => r.request().method() === 'GET'
        ? r.fulfill({ status: 200, json: { status: 'error', error_code: 'internal_error' } }) : r.fallback())
      await land(page); await guidedResults(page)
      await page.getByTestId('ic-error').waitFor()
      await shot(page, 'guided-3-could-not-finish', scheme, page.getByTestId('hub-chapter-investment'))
      // A refusal that states its own reason (issue 03's money-year sentence): shown whole.
      await page.unroute(IC)
      await page.route(IC, r => r.request().method() === 'GET'
        ? r.fulfill({ status: 200, json: { status: 'refused', error_code: 'campus_assets_money_year_mismatch',
            error: "campus_assets_money_year_mismatch: the campus assets are priced in 2026 money, the case's price data "
              + 'are 2020; reprice the campus asset library in 2020, or state the finance inputs in 2026 money' } })
        : r.fallback())
      await land(page); await guidedResults(page)
      await page.getByTestId('ic-error').waitFor()
      await shot(page, 'guided-4-could-not-finish-reason', scheme, page.getByTestId('hub-chapter-investment'))
      await page.unroute(IC)
      // With the hub study's results (the repo's test fixtures), the panel sits under the headline NPV.
      const fx = await page.evaluate(async () => {
        const { REPORT, review } = await import('/src/pages/hubDesign/testFixtures.ts')
        return { report: REPORT, review: review() }
      })
      const json = body => r => r.request().method() === 'GET' ? r.fulfill({ status: 200, json: body }) : r.fallback()
      await page.route(u => u.pathname === '/api/results/eh_study',
        json({ status: 'done', archetype: 'weak_flexible', report: fx.report, edited_since_study: false }))
      await page.route(u => u.pathname === '/api/results/eh_review', json(fx.review))
      await page.route(u => u.pathname === '/api/results/eh_reference_design', json(fx.report))
      await land(page)
      await page.evaluate(async () => {
        const { useHubDesignStore } = await import('/src/pages/hubDesign/hubDesignStore.ts')
        const { useUIStore } = await import('/src/store/uiStore.ts')
        useUIStore.getState().setSlidePanel('hubDesign')
        useHubDesignStore.getState().setStep('results', { user: true })
      })
      const head = page.getByTestId('hub-results-headline')
      await head.waitFor(); await page.getByTestId('ic-run').waitFor(); await page.waitForTimeout(1200)
      await shot(page, 'guided-5-headline-before-run', scheme, head)
    } catch (e) { problems.push(`${scheme} guided: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-guided-${scheme}.png`) }) }
    await ctx.close();

    // Expert, IPP template: the Lifecycle-cost NPV note.
    ({ ctx, page } = await open(scheme, { project: IPP.project, mode: 'expert' }))
    try {
      await page.route(u => u.pathname === '/api/results/investment_case/report',
        r => r.fulfill({ status: 200, json: IPP_REPORT }))
      await page.route(IC, r => r.request().method() === 'GET'
        ? r.fulfill({ status: 200, json: { status: 'done' } }) : r.fallback())
      await land(page)
      await page.evaluate(async () => {
        const { showResultsTab } = await import('/src/layout/flowNavigation.ts')
        showResultsTab('investment', { section: 'case' })
      })
      await page.waitForTimeout(2500)
      const caseTab = page.getByRole('tab', { name: 'Investment case' })
      if (await caseTab.count()) await caseTab.first().click()
      await page.getByTestId('ic-headline-aside-lifecycle_npv').waitFor(); await page.waitForTimeout(800)
      await shot(page, 'expert-4-lifecycle-note', scheme, page.getByTestId('ic-headlines'))
    } catch (e) { problems.push(`${scheme} expert: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-expert-${scheme}.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
