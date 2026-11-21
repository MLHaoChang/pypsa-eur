// Template-e2e 22 S2 shots: the investment case's run panel in Expert, before
// a run and refused with two needs. Real API (the data-centre template, not
// solved: the start is refused with not_solved and its finance inputs are
// unset; the busy 409 is mocked). Run: node .scratch/template-e2e/shots/22-s2/shots.mjs (app on :8000/:5173).
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || HERE
const PROJECT = 'te22-investment'
const TEMPLATE = 'eh_datacenter'
const MODE = process.env.MODE || 'expert'
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
    await ctx.addInitScript(({ project, scheme, mode }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', scheme)
    }, { project: PROJECT, scheme, mode: MODE })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${scheme}: page error: ${e.message}`))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.waitForTimeout(2500)
    const hideToasts = () => page.evaluate(() => {
      for (const n of document.querySelectorAll('div[style*="z-index: 9999"]')) n.style.visibility = 'hidden'
    })
    const view = async name => { await hideToasts(); const f = path.join(OUT, `${MODE}-${name}-${scheme}.png`); await page.screenshot({ path: f }); written.push(f) }
    try {
      await page.evaluate(async () => {
        const { showResultsTab } = await import('/src/layout/flowNavigation.ts')
        showResultsTab('investment', { section: 'case' })
      })
      await page.waitForTimeout(2500)
      const caseTab = page.getByRole('tab', { name: 'Investment case' })
      if (await caseTab.count()) await caseTab.first().click()
      await page.getByTestId('ic-run-panel').waitFor()
      await page.getByTestId('ic-run').waitFor(); await page.waitForTimeout(1000)
      await view('1-before-run')
      await page.getByTestId('ic-run').click()
      await page.getByTestId('ic-need-solve').waitFor()
      await page.waitForTimeout(1500)
      await view('2-refused-needs')
      // A busy 409 is a plain sentence (routers/results.py _refuse_if_mesh_busy);
      // a solve or another study must be running to get it, so it is mocked here.
      await page.route(u => u.pathname === '/api/results/investment_case', r => r.request().method() === 'POST'
        ? r.fulfill({ status: 409, json: { detail: 'a frontier study is running — wait for it to finish' } }) : r.fallback())
      await page.getByTestId('ic-run').click()
      await page.getByTestId('ic-blocked').waitFor()
      await view('3-wait')
    } catch (e) { problems.push(`${scheme}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${scheme}.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
