// project-io 08 shot set 2: a Study project (336 h, k = 2) in the real app.
// Sidebar and header on a Study and on a non-Study project, the panel before
// a run, dispatch progress "window k of n" with its clock, then completion,
// the bundles list and one bundle download. Real API, nothing mocked.
// Run: node .scratch/project-io/shots-08/shots.mjs (app on :8000/:5173).
// CAP_S (default 900) caps the wait for completion.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from '../../../pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BASE = 'http://127.0.0.1:5173'
const OUT = process.env.OUT || HERE
const STUDY = process.env.STUDY || `pio08-study-${Date.now() % 100000}`
const OTHER = 'pio08-network'
const CAP_S = Number(process.env.CAP_S || 900)
const problems = []
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

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
  const made = await login.evaluate(async ({ study, other }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const { gridspineApi } = await import('/src/api/gridspine.ts')
    const list = await projectsApi.list().catch(() => [])
    const out = {}
    if (!list.some(p => p.name === other)) {
      try { await projectsApi.createFromTemplate('eh_datacenter', other); out.other = 'made' } catch (e) { out.other = `failed: ${e.message}` }
    } else out.other = 'exists'
    try {
      const r = await gridspineApi.createStudy(study, { hours: 336, k: 2, window: 168, overlap: 24, screen: true })
      out.study = `made ${r.id}`
    } catch (e) { out.study = `failed: ${e.message}` }
    return out
  }, { study: STUDY, other: OTHER })
  log('projects', JSON.stringify(made))
  const auth = await owner.storageState()
  await owner.close()

  const open = async (project, mode, panel) => {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'light', acceptDownloads: true })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ project, mode }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { project, mode })
    const page = await ctx.newPage()
    page.setDefaultTimeout(20000)
    page.on('pageerror', e => problems.push(`${project}/${mode}: page error: ${e.message}`))
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.waitForTimeout(2500)
    if (panel) await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('gridspine') })
    await page.waitForTimeout(1000)
    return { ctx, page }
  }
  const shot = async (page, name) => {
    await page.evaluate(() => { for (const n of document.querySelectorAll('div[style*="z-index: 9999"]')) n.style.visibility = 'hidden' })
    const f = path.join(OUT, `${name}.png`); await page.screenshot({ path: f }); log('shot', f)
  }

  // 1. Non-Study project: the full hub sidebar stays, and Expert keeps Run LOPF
  //    (Guided shows the hub stepper, which never had that button).
  for (const mode of ['expert', 'guided']) {
    const { ctx, page } = await open(OTHER, mode, false)
    const lopf = await page.getByRole('button', { name: /Run LOPF/ }).count()
    log(`non-study ${mode}: Run LOPF buttons`, lopf)
    if (mode === 'expert' && !lopf) problems.push('non-study expert: no Run LOPF')
    await shot(page, `01-nonstudy-${mode}`)
    await ctx.close()
  }

  // 2. Study project: sidebar, header, panel before the run (Guided, then Expert).
  const g = await open(STUDY, 'guided', true)
  if (await g.page.getByRole('button', { name: /Run LOPF/ }).count()) problems.push('study guided: Run LOPF shown')
  await shot(g.page, '02-study-guided')
  await g.ctx.close()
  const { ctx, page } = await open(STUDY, 'expert', true)
  if (await page.getByRole('button', { name: /Run LOPF/ }).count()) problems.push('study expert: Run LOPF shown')
  await shot(page, '03-study-expert-before')

  // 3. Run study; watch dispatch progress.
  await page.getByRole('button', { name: 'Run study' }).click()
  const t0 = Date.now()
  const seen = new Set()
  let shotWindow = 0
  let done = false
  while ((Date.now() - t0) / 1000 < CAP_S) {
    const txt = (await page.locator('[data-testid="stage-dispatch"]').innerText().catch(() => '')).replace(/\s+/g, ' ')
    const m = txt.match(/window (\d+) of (\d+)/)
    if (m && !seen.has(m[0])) {
      seen.add(m[0]); log('dispatch:', txt)
      if (shotWindow < 2) { await page.waitForTimeout(3000); await shot(page, `04-dispatch-window-${m[1]}-of-${m[2]}`); shotWindow++ }
    }
    const st = await page.evaluate(async (s) => { const { gridspineApi } = await import('/src/api/gridspine.ts'); return (await gridspineApi.status(s)).status }, STUDY).catch(() => '')
    if (st === 'completed' || st === 'failed' || st === 'aborted') { log('status', st, `${Math.round((Date.now() - t0) / 1000)} s`); done = st === 'completed'; if (!done) problems.push(`study ${st}`); break }
    await page.waitForTimeout(4000)
  }
  if (!done && !problems.some(p => p.startsWith('study '))) problems.push(`not completed within ${CAP_S} s`)
  if (seen.size < 2) problems.push(`dispatch progress seen: ${[...seen].join(', ') || 'none'}`)
  if (done) {
    await page.waitForTimeout(5000)
    await shot(page, '05-completed')
    const hourRows = await page.getByTitle(/Download the handoff bundle/).count()
    log('bundle download buttons', hourRows)
    if (hourRows !== 8) problems.push(`expected 8 bundle hours, got ${hourRows}`)
    const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.getByTitle(/Download the handoff bundle/).first().click()])
    const f = path.join(OUT, '..', `${dl.suggestedFilename()}`)
    const tmp = await dl.path(); const size = fs.statSync(tmp).size
    log('downloaded', dl.suggestedFilename(), `${size} bytes`)
    if (size < 1000) problems.push(`bundle only ${size} bytes`)
    void f
    await page.waitForTimeout(2500)
    const abort = await page.getByRole('button', { name: /Abort/ }).count()
    const solving = await page.getByText(/Solving…/).count()
    log('after completion: Abort', abort, 'Solving…', solving)
    await shot(page, '06-completed-downloaded')
  }
  await ctx.close()
} finally {
  await browser.close()
}
if (problems.length) { console.log('PROBLEMS:\n' + problems.join('\n')); process.exitCode = 1 } else console.log('E2E OK')
