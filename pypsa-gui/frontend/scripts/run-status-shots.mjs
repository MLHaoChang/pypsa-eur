// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for run progress slice 2: the run strip's status
 * line (under the header) in each state, in Guided and Expert, at 1280 and 1024 px. Drives the
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
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'run-status-shots-'))
const HEADER = 104
const JOB = '00000000-0000-4000-8000-000000000001'
const now = () => Date.now() / 1000

// Each state: the queue's job for the project (or none), its estimate, the goal study.
const STATES = {
  queued: { job: { status: 'queued', position: 2, started_at: null }, estimate: null, study: null },
  eta: { job: { status: 'running', started_at: () => now() - 134 }, estimate: { seconds: 400, samples: 3 }, study: null },
  unknown: { job: { status: 'running', started_at: () => now() - 74 }, estimate: { seconds: null, samples: 1 }, study: null },
  overrun: { job: { status: 'running', started_at: () => now() - 580 }, estimate: { seconds: 360, samples: 5 }, study: null },
  campus: { job: { status: 'running', kind: 'campus', started_at: () => now() - 95 }, estimate: { seconds: 210, samples: 2 }, study: null },
  study: { job: null, estimate: null,
    study: () => ({ status: 'running', started_at: now() - 29, budget_solves: 30,
      report: { pipeline: { solves_consumed: 11, budget_solves: 30 } } }) },
}
const EXPECT = {
  queued: 'Optimisation · Waiting in queue #2',
  eta: 'about 4 min left',
  unknown: 'first runs: no estimate yet',
  overrun: 'longer than usual (usually about 6 min)',
  campus: 'Campus study · Running',
  study: 'Reliability study · Studying · step 12 of up to 30',
}

await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  await page.emulateMedia({ reducedMotion: 'reduce' })

  const { email, password } = devCredentials()
  await page.goto(`${BASE}/login`)
  await page.fill('input[type=email]', email)
  await page.fill('input[type=password]', password)
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/projects/)
  const name = `run-status-${Date.now().toString(36)}`
  // Through the app's own client, which sends the CSRF header.
  const made = await page.evaluate(async (n) => {
    const { default: client } = await import('/src/api/client.ts')
    try {
      await client.post(`/projects/from_template/eh_microgrid_staged?name=${encodeURIComponent(n)}`)
      return 'ok'
    } catch (e) { return String(e?.response?.status ?? e) }
  }, name)
  if (made !== 'ok') throw new Error(`template create: ${made}`)

  let state = 'queued'
  const job = () => {
    const j = STATES[state].job
    if (!j) return null
    return {
      id: JOB, project_id: name, project_key: null, kind: j.kind ?? 'solve', status: j.status,
      position: j.position ?? null, objective: null, solve_time: null, condition: null, error: null,
      enqueued_at: now() - 600, started_at: typeof j.started_at === 'function' ? j.started_at() : j.started_at,
      finished_at: null,
    }
  }
  await page.route(u => u.pathname === '/api/simulation/queue', route => {
    if (route.request().method() !== 'GET') return route.continue()
    const j = job()
    return route.fulfill({ json: { jobs: j ? [j] : [], running: j?.status === 'running' ? [JOB] : [], paused: false } })
  })
  await page.route(u => u.pathname === `/api/simulation/queue/${JOB}/estimate`, route => {
    const e = STATES[state].estimate
    return route.fulfill({ json: { kind: 'solve', run_ids: [], ...(e ?? { seconds: null, samples: 0 }) } })
  })
  await page.route(u => u.pathname.startsWith(`/api/simulation/queue/${JOB}/log`), route =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))
  await page.route(u => u.pathname === '/api/results/eh_study', async route => {
    const s = STATES[state].study
    if (route.request().method() !== 'GET' || !s) return route.continue()
    return route.fulfill({ json: s() })
  })

  for (const name_ of Object.keys(STATES)) {
    state = name_
    for (const mode of ['guided', 'expert']) {
      for (const width of [1280, 1024]) {
        await page.setViewportSize({ width, height: 800 })
        await page.goto(`${BASE}/app?project=${encodeURIComponent(name)}`)
        await page.getByTestId(`ui-mode-${mode}`).click()
        const line = page.getByTestId('run-status')
        await line.waitFor({ timeout: 20000 })
        await page.waitForTimeout(2500)
        const title = (await line.getAttribute('title')) ?? ''
        if (!title.includes(EXPECT[name_])) throw new Error(`${name_}/${mode}/${width}: title "${title}" lacks "${EXPECT[name_]}"`)
        const shown = (await line.innerText()).replace(/\s+/g, ' ').trim()
        const box = await line.boundingBox()
        await page.mouse.move(width / 2, 600)
        const file = path.join(out, `run-status-${name_}-${mode}-${width}.png`)
        await page.screenshot({ path: file, clip: { x: 0, y: 0, width, height: HEADER } })
        console.log(`${file} | shown: ${shown} | right edge ${Math.round(box.x + box.width)}/${width}`)
      }
    }
  }
} finally {
  await browser.close()
}
