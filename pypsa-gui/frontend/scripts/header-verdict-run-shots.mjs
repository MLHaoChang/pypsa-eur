// template-e2e 17: the one Run entry in Expert. Header Run reads "Run study" (enabled), blocked with a
// reason, and "Run LOPF" on a generation-only site. Built app + fixture backend; the flow scope and
// the reliability goal read are mocked.  OUT=<dir> node scripts/header-verdict-run-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { openBrowser, signIn, startBackend, stopBackend, tmpdir } from './template-journeys-lib.mjs'

const PROJECT = 'Header verdict run shots'
const OUT = process.env.OUT || await tmpdir('header-verdict-')
await fs.mkdir(OUT, { recursive: true })
const { child, base, credentials_file: file } = await startBackend(path.join(OUT, 'storage'))
const browser = await openBrowser()
let failed = false
const registry = status => ({
  scope: { version: 1, source: 'project', preset_id: 'p', groups: ['capacity', 'reliability'],
    options: { capacity: [], grid: [], control: [], reliability: ['goal_study'], commercial: [], handoff: [] } },
  cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study'], sections: [], chip: null,
    options: [{ id: 'goal_study', label: 'Goal study', status, reason: status === 'available' ? null : 'Not available here' }] }],
  preset_id: 'p', preset_label: 'P',
  preset: { version: 1, groups: ['capacity', 'reliability'], options: { capacity: [], grid: [], control: [], reliability: ['goal_study'], commercial: [], handoff: [] } },
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
  const shots = [
    ['17-run-study-enabled', 'available', false, /Run study/, false],
    ['17-run-study-blocked', 'available', true, /Run study/, true],
    ['17-run-lopf-generation-only', 'not_yet_available', false, /Run LOPF/, false],
  ]
  for (const [name, status, breakGoal, label, blocked] of shots) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: 'light', storageState: state })
    await context.addInitScript(p => { try { localStorage.setItem('theme', 'light'); localStorage.setItem('network-diagram:ui-mode', 'expert'); localStorage.setItem('network-diagram:ui-mode-explicit', '1'); localStorage.setItem('network-diagram:current-project', p) } catch {} }, PROJECT)
    const page = await context.newPage()
    page.setDefaultTimeout(30000)
    await page.route(/\/flow\/scope(\?.*)?$/, route => route.request().method() === 'GET' ? route.fulfill({ json: registry(status) }) : route.continue())
    await page.route(/\/simulation\/solver_config(\?.*)?$/, async route => {
      if (route.request().method() !== 'GET') return route.continue()
      const res = await route.fetch(); const j = await res.json().catch(() => ({}))
      await route.fulfill({ json: { ...j, voll: breakGoal ? 0 : 10000 } })
    })
    await page.route(/\/flow(\?.*)?$/, async route => {
      if (route.request().method() !== 'GET') return route.continue()
      const res = await route.fetch(); const j = await res.json().catch(() => ({}))
      await route.fulfill({ json: { ...j, run_ready: true, run_blocker: null, run_verdict: { ready: true, blocker: null }, verdicts: { ...j.verdicts, optimisation: { ready: true, blocker: null } } } })
    })
    await page.goto(`${base}/app`)
    await page.getByRole('button', { name: 'Skip tour' }).click({ timeout: 4000 }).catch(() => {})
    await page.getByRole('button', { name: /^Run (study|LOPF)/ }).first().waitFor({ timeout: 40000 }).catch(async e => { await page.screenshot({ path: path.join(OUT, 'debug.png') }); throw e })
    if (blocked) await page.getByTestId('run-blocker').waitFor({ timeout: 20000 })
    await page.waitForTimeout(2500)
    const text = await page.locator('header').first().innerText()
    if (!label.test(text)) throw new Error(`${name}: header reads ${JSON.stringify(text)}`)
    if (blocked !== (await page.getByTestId('run-blocker').count() > 0)) throw new Error(`${name}: run-blocker mismatch: ${JSON.stringify(text)}`)
    await page.screenshot({ path: path.join(OUT, `${name}.png`), clip: { x: 0, y: 0, width: 1280, height: 110 } })
    await context.close()
  }
  console.log('shots in', OUT)
} catch (e) { failed = true; console.error(e) } finally { await browser.close(); await stopBackend(child) }
process.exit(failed ? 1 : 0)
