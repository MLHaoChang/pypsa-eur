// template-e2e 25: the Guided step rail while a study runs and the flow is deferred: steps show the
// spinner + "run in progress". Built app + fixture backend; the study record and flow are mocked.
//   OUT=<dir> node scripts/guided-rail-run-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { openBrowser, signIn, startBackend, stopBackend, tmpdir } from './template-journeys-lib.mjs'

const PROJECT = 'Guided rail run shots'
const OUT = process.env.OUT || await tmpdir('guided-rail-run-')
await fs.mkdir(OUT, { recursive: true })
const { child, base, credentials_file: file } = await startBackend(path.join(OUT, 'storage'))
const browser = await openBrowser()
let failed = false
const SECTIONS = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial', 'scenarios', 'readiness', 'results']
const flowPayload = () => ({
  sections: SECTIONS.map(section => ({ section, label: section, state: section === 'results' ? null : 'unknown', required: true, items: [], summary: null })),
  run_ready: true, run_blocker: null, run_request: { optimisation: true, study: true },
  verdicts: { optimisation: { ready: true, blocker: null }, study: null },
  notes: [], deferred: true, deferred_reason: 'study running',
})
try {
  const setup = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const sp = await setup.newPage()
  sp.setDefaultTimeout(60000)
  await signIn(setup, sp, base, file)
  const state = await setup.storageState()
  const csrf = (await setup.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
  const headers = csrf ? { 'X-CSRF-Token': csrf } : {}
  await setup.request.post(`${base}/api/network/reset`, { headers })
  const saved = await setup.request.post(`${base}/api/projects/${encodeURIComponent(PROJECT)}?force=true`, { headers })
  if (!saved.ok()) throw new Error(`save: ${saved.status()} ${await saved.text()}`)
  await setup.close()
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: 'light', storageState: state })
  await context.addInitScript(p => { try {
    localStorage.setItem('theme', 'light'); localStorage.setItem('network-diagram:ui-mode', 'guided')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1'); localStorage.setItem('network-diagram:current-project', p)
    localStorage.setItem(`pypsa-guide-offered:hub_design:${p}`, '1')
  } catch {} }, PROJECT)
  const page = await context.newPage()
  page.setDefaultTimeout(30000)
  await page.route(/\/results\/eh_study(\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: { status: 'running', started_at: Date.now() / 1000 - 60, budget_solves: 30 } }) : route.continue())
  await page.route(/\/flow(\?.*)?$/, route => route.request().method() === 'GET' ? route.fulfill({ json: flowPayload() }) : route.continue())
  await page.goto(`${base}/app`)
  await page.getByRole('button', { name: 'Skip tour' }).click({ timeout: 4000 }).catch(() => {})
  const rail = page.getByTestId('hub-rail')
  await rail.waitFor({ timeout: 40000 }).catch(async e => { await page.screenshot({ path: path.join(OUT, 'debug.png') }); throw e })
  await page.waitForFunction(() => document.querySelector('[data-testid="hub-rail"]')?.innerHTML.includes('run in progress'), null, { timeout: 20000 })
  await page.waitForTimeout(1500)
  const n = await page.locator('[data-testid="hub-rail"] [aria-label*="run in progress"]').count()
  console.log('run-in-progress steps:', n)
  const box = await rail.boundingBox()
  const bottom = Math.ceil(box.y + box.height) + 6
  await page.screenshot({ path: path.join(OUT, '25-rail-run-in-progress.png'), clip: { x: 0, y: 0, width: 1280, height: bottom } })
  await context.close()
  console.log('shots in', OUT)
} catch (e) { failed = true; console.error(e) } finally { await browser.close(); await stopBackend(child) }
process.exit(failed ? 1 : 0)
