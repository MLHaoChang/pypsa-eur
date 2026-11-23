// Template-e2e 40 shots: one start refusal lists every need it can tell
// (`detail.needs`). Real API, no mocks: the data-centre template is not solved,
// and the script clears this throwaway project's value flows and finance inputs
// (PUT /simulation/commercial/value_flows null, PUT /simulation/finance null).
// So the start is refused with not_solved and lists the solve, the site
// connection and the finance inputs in one checklist. Before issue 40 it listed
// the solve only. Guided: the Results card's Investment
// tile. Expert: Results → Investment → Investment case.
// Run: node .scratch/template-e2e/shots/40/shots.mjs (app on :8000/:5173), then
// scripts/coord/shots.sh on each PNG to downscale it (SHOTS-HOME-1).
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || HERE
const DC = { project: 'te40-investment', template: 'eh_datacenter' }
const problems = []
const written = []

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
  for (const p of [DC]) {
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
  async function clearInputs(page) {
    const status = await page.evaluate(async () => {
      const { financeApi } = await import('/src/api/finance.ts')
      const { commercialApi } = await import('/src/api/commercial.ts')
      const v = await commercialApi.getValueFlows()
      if (v.value_flows) await commercialApi.putValueFlows(null, v.digest)
      const f = await financeApi.getFinance()
      return f.status === 'not_set' ? f.status : (await financeApi.putFinance(null, f.digest)).status
    })
    if (status !== 'not_set') problems.push(`finance inputs not cleared: ${status}`)
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
    let { ctx, page } = await open(scheme, { project: DC.project, mode: 'guided' })
    try {
      await land(page); await clearInputs(page); await guidedResults(page)
      await page.getByTestId('ic-run').click()
      await page.getByTestId('ic-need-finance').waitFor(); await page.waitForTimeout(800)
      console.log(scheme, 'guided needs', await page.getByTestId('ic-needs').locator('li').evaluateAll(l => l.map(x => x.dataset.testid)))
      await shot(page, 'guided-refused-every-need', scheme, page.getByTestId('hub-chapter-investment'))
    } catch (e) { problems.push(`${scheme} guided: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-guided-${scheme}.png`) }) }
    await ctx.close();

    ({ ctx, page } = await open(scheme, { project: DC.project, mode: 'expert' }))
    try {
      await land(page)
      await page.evaluate(async () => {
        const { showResultsTab } = await import('/src/layout/flowNavigation.ts')
        showResultsTab('investment', { section: 'case' })
      })
      await page.waitForTimeout(2500)
      const caseTab = page.getByRole('tab', { name: 'Investment case' })
      if (await caseTab.count()) await caseTab.first().click()
      await page.getByTestId('ic-run').click()
      await page.getByTestId('ic-need-finance').waitFor(); await page.waitForTimeout(1200)
      console.log(scheme, 'expert needs', await page.getByTestId('ic-needs').locator('li').evaluateAll(l => l.map(x => x.dataset.testid)))
      await shot(page, 'expert-refused-every-need', scheme)
    } catch (e) { problems.push(`${scheme} expert: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-expert-${scheme}.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
