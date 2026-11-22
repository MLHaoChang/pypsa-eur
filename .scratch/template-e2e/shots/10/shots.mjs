// Template-e2e 10 shots: the three VOLL refusals where the user meets them, in
// Expert → Results → Adequacy (cost vs availability, planning loop) and FMEA
// (B/C sweep). Real API, the data-centre template with VOLL set to 0 and a
// reliability goal set (so the loop's button is live). Nothing is mocked.
// Run: node .scratch/template-e2e/shots/10/shots.mjs (app on :8000/:5173).
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || HERE
const PROJECT = 'te10-voll-refusals'
const TEMPLATE = 'eh_datacenter'
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
    await ctx.addInitScript(({ project, scheme }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', 'expert')
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', scheme)
    }, { project: PROJECT, scheme })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${scheme}: page error: ${e.message}`))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.waitForTimeout(5000)
    const set = await page.evaluate(async () => {
      const client = (await import('/src/api/client.ts')).default
      const cfg = (await client.get('/simulation/solver_config')).data
      const r = await client.put('/simulation/solver_config', { ...cfg, voll: 0, target_lole_h: 3 })
      return `voll=${r.data.voll} target=${r.data.target_lole_h}`
    })
    console.log(scheme, 'solver config', set)
    const toastText = () => page.locator('div[style*="z-index: 9999"] > div:visible').allInnerTexts()
    // Hide the toasts already read, so each shot shows only its own refusal.
    const clearToasts = () => page.evaluate(() => {
      for (const box of document.querySelectorAll('div[style*="z-index: 9999"]')) for (const n of box.children) n.style.display = 'none'
    })
    const view = async name => { const f = path.join(OUT, `${name}-${scheme}.png`); await page.screenshot({ path: f }); written.push(f) }
    const tab = async id => {
      await page.evaluate(async id => {
        const { showResultsTab } = await import('/src/layout/flowNavigation.ts')
        showResultsTab(id)
      }, id)
      await page.waitForTimeout(2000)
    }
    const expectToast = async (needle, name) => {
      await page.getByText(needle, { exact: false }).first().waitFor()
      await page.waitForTimeout(800)
      const t = (await toastText()).filter(Boolean).join(' | ')
      console.log(scheme, name, '→', t)
      await view(name)
      await clearToasts()
    }
    try {
      await clearToasts()
      await tab('adequacy')
      const frontier = page.getByTestId('frontier-panel')
      await frontier.scrollIntoViewIfNeeded()
      await frontier.getByRole('button', { name: /Cost vs availability/ }).click()
      await frontier.getByRole('button', { name: 'Run study' }).click()
      await expectToast('to run the cost vs availability study', '1-cost-vs-availability')

      const loop = page.getByTestId('loop-panel')
      await loop.scrollIntoViewIfNeeded()
      await page.getByTestId('loop-toggle').click()
      await page.getByTestId('loop-run').click()
      await page.getByTestId('loop-blocked').waitFor()
      console.log(scheme, 'loop-blocked →', await page.getByTestId('loop-blocked').innerText())
      await page.getByTestId('loop-blocked').scrollIntoViewIfNeeded()
      await expectToast('to run the reliability-targeted planning loop', '2-planning-loop')

      await tab('fmea')
      await page.getByTestId('fmea-sweep').click()
      await expectToast('to run the contingency sweep', '3-contingency-sweep')
    } catch (e) { problems.push(`${scheme}: ${e.message.split('\n')[0]}`); await page.screenshot({ path: path.join(OUT, `failed-${scheme}.png`) }) }
    await ctx.close()
  }
} finally { await browser.close() }
console.log(written.join('\n')); console.log('problems', problems)
