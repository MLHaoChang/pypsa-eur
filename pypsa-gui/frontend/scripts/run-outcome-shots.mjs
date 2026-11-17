// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for run progress slice 2b: the run strip after a
 * run ends (finished + Open results, failed + See why, interrupted, stopped)
 * and the Runs indicator open, in Guided and Expert, light and dark. Each shot
 * first serves the job as running, so the strip has seen it going on, then
 * serves its end. Drives the
 * running app (API on :8000, Vite on :5173), signs in as the dev admin,
 * creates a project from a template, and rewrites `GET /api/simulation/queue`,
 * the job estimate and `GET /api/results/eh_study`. No solve and no model call.
 * node scripts/run-status-shots.mjs [out-dir]   (default: a new temp dir)
 */
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.SHOTS_BASE || 'http://127.0.0.1:5173'
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'run-outcome-shots-'))
const HEADER = 104
const JOB = '00000000-0000-4000-8000-000000000001'
const now = () => Date.now() / 1000

const OTHER = '00000000-0000-4000-8000-000000000002'
// [shot name, end status, mode, theme, width, click]
const SHOTS = [
  ['finished', 'completed', 'guided', 'light', 1280], ['finished', 'completed', 'expert', 'light', 1280],
  ['finished', 'completed', 'guided', 'light', 1024], ['finished', 'completed', 'expert', 'light', 1024],
  ['finished', 'completed', 'guided', 'dark', 1280], ['finished', 'completed', 'expert', 'dark', 1280],
  ['failed-why', 'failed', 'guided', 'light', 1280, 'run-outcome-why'], ['failed', 'failed', 'expert', 'light', 1280],
  ['failed-why', 'failed', 'guided', 'dark', 1280, 'run-outcome-why'], ['failed', 'failed', 'expert', 'dark', 1280],
  ['interrupted-why', 'interrupted', 'guided', 'light', 1280, 'run-outcome-why'],
  ['stopped', 'aborted', 'guided', 'light', 1280],
  ['runs-open', null, 'guided', 'light', 1280, 'runs-indicator'], ['runs-open', null, 'guided', 'dark', 1280, 'runs-indicator'],
]
const HEIGHT = 300

await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' })

  const { email, password } = devCredentials()
  await page.goto(`${BASE}/login`)
  await page.fill('input[type=email]', email)
  await page.fill('input[type=password]', password)
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/projects/)
  const name = `run-outcome-${Date.now().toString(36)}`
  // Through the app's own client, which sends the CSRF header.
  const made = await page.evaluate(async (n) => {
    const { default: client } = await import('/src/api/client.ts')
    try {
      await client.post(`/projects/from_template/eh_microgrid_staged?name=${encodeURIComponent(n)}`)
      return 'ok'
    } catch (e) { return String(e?.response?.status ?? e) }
  }, name)
  if (made !== 'ok') throw new Error(`template create: ${made}`)

  let end = null
  const mk = (id, project, status, started) => ({
    id, project_id: project, project_key: null, kind: 'solve', status, position: null, objective: null,
    solve_time: null, condition: null,
    error: status === 'failed' ? 'The solver found the model infeasible: demand at bus Site cannot be met in hour 1,412.' : null,
    enqueued_at: now() - 700, started_at: started, finished_at: status === 'running' ? null : now() - 2,
  })
  await page.route(u => u.pathname === '/api/simulation/queue', route => {
    if (route.request().method() !== 'GET') return route.continue()
    const started = now() - 372
    const jobs = [mk(JOB, name, end ?? 'running', started)]
    if (!end) jobs.push(mk(OTHER, 'Mining Concentrator', 'running', now() - 1500))
    return route.fulfill({ json: { jobs, running: jobs.filter(j => j.status === 'running').map(j => j.id), paused: false } })
  })
  await page.route(u => u.pathname.endsWith('/estimate'), route =>
    route.fulfill({ json: { kind: 'solve', run_ids: [], seconds: 400, samples: 3 } }))
  await page.route(u => u.pathname.includes('/api/simulation/queue/') && u.pathname.includes('/log'), route =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))

  for (const [shot, status, mode, theme, width, click] of SHOTS) {
    end = null
    await page.evaluate(t => localStorage.setItem('network-diagram:theme', t), theme)
    await page.setViewportSize({ width, height: 800 })
    await page.goto(`${BASE}/app?project=${encodeURIComponent(name)}`)
    await page.getByTestId(`ui-mode-${mode}`).click()
    await page.getByTestId('run-status').waitFor({ timeout: 20000 })
    if (status) {
      end = status
      await page.getByTestId('run-outcome').waitFor({ timeout: 20000 })
    }
    if (click) await page.getByTestId(click).click()
    await page.waitForTimeout(2500)
    const what = status
      ? (await page.getByTestId('run-outcome').innerText()).replace(/\s+/g, ' ').trim()
      : (await page.getByTestId('runs-popover').innerText()).replace(/\s+/g, ' ').trim()
    const suffix = theme === 'dark' ? '-dark' : ''
    const file = path.join(out, `run-outcome-${shot}-${mode}-${width}${suffix}.png`)
    await page.screenshot({ path: file, clip: { x: 0, y: 0, width, height: HEIGHT } })
    console.log(`${file} | ${what}`)
  }
} finally {
  await browser.close()
}
