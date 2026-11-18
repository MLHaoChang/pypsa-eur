// Run progress slice 3 shots: the run notices (finished, failed + See why,
// interrupted, four stacked, the away summary), the "Notify me" link on the
// running line and the PREFERENCES "Browser notifications" setting, open and
// blocked. Guided and Expert, light and dark, 1024 and 1280 px.
//
//   node scripts/run-notice-shots.mjs [out-dir]
//
// Needs the API on :8000 and Vite on :5173. The queue listing is stubbed, so
// the runs here are made up; the project is real (from a template).
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.SHOTS_BASE || 'http://127.0.0.1:5173'
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'run-notice-shots-'))
const now = () => Date.now() / 1000
const id = n => `00000000-0000-4000-8000-00000000000${n}`

// [shot, scenario, mode, theme, width, click]
const SHOTS = []
for (const mode of ['guided', 'expert']) {
  for (const [theme, width] of [['light', 1280], ['light', 1024], ['dark', 1280]]) {
    SHOTS.push(['finished', 'finished', mode, theme, width])
    SHOTS.push(['failed', 'failed', mode, theme, width, mode === 'guided' ? 'run-notice-why' : null])
  }
  SHOTS.push(['interrupted', 'interrupted', mode, 'light', 1280])
  SHOTS.push(['stacked', 'stacked', mode, 'light', 1280])
  SHOTS.push(['stacked', 'stacked', mode, 'dark', 1280])
  SHOTS.push(['away', 'away', mode, 'light', 1280, 'run-notice-show'])
  SHOTS.push(['away', 'away', mode, 'dark', 1280, 'run-notice-show'])
  SHOTS.push(['optin', 'optin', mode, 'light', 1280])
  SHOTS.push(['optin', 'optin', mode, 'light', 1024])
  SHOTS.push(['optin', 'optin', mode, 'dark', 1280])
}
SHOTS.push(['pref-off', 'pref', 'expert', 'light', 1280])
SHOTS.push(['pref-off', 'pref', 'expert', 'dark', 1280])
SHOTS.push(['pref-blocked', 'blocked', 'expert', 'light', 1280])
SHOTS.push(['pref-blocked', 'blocked', 'expert', 'dark', 1280])

await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
try {
  const { email, password } = devCredentials()
  let name = null
  let scenario = null
  let ended = false
  let permission = 'default'

  const mk = (n, project, status, kind, over = {}) => ({
    id: id(n), project_id: project, project_key: null, kind, status, position: null, objective: null,
    solve_time: null, condition: null,
    error: status === 'failed' ? 'The solver found the model infeasible: demand at bus Site cannot be met in hour 1,412.' : null,
    enqueued_at: now() - 900, started_at: now() - 372, finished_at: status === 'running' || status === 'queued' ? null : now() - 2,
    can_dismiss: status !== 'running', started_by_me: true, ...over,
  })
  const jobsFor = () => {
    const run = s => (ended ? s : 'running')
    switch (scenario) {
      case 'finished': return [mk(1, name, run('completed'), 'campus')]
      case 'failed': return [mk(1, name, run('failed'), 'campus')]
      case 'interrupted': return [mk(1, name, run('interrupted'), 'solve')]
      case 'stacked': return [
        mk(1, name, run('completed'), 'campus'), mk(2, 'Mining Concentrator', run('failed'), 'solve'),
        mk(3, 'Harbour Microgrid', run('interrupted'), 'solve'), mk(4, 'Data Centre North', run('failed'), 'gridspine'),
      ]
      case 'away': return [
        mk(1, name, 'completed', 'campus', { finished_at: now() - 1800 }),
        mk(2, 'Mining Concentrator', 'failed', 'solve', { finished_at: now() - 900 }),
      ]
      case 'optin': return [mk(1, name, 'running', 'campus')]
      default: return []
    }
  }

  for (const [shot, scen, mode, theme, width, click] of SHOTS) {
    const context = await browser.newContext({ viewport: { width, height: 800 }, deviceScaleFactor: 2 })
    permission = scen === 'blocked' ? 'denied' : 'default'
    await context.addInitScript(p => {
      if (!('Notification' in window)) return
      Object.defineProperty(Notification, 'permission', { get: () => p })
    }, permission)
    const page = await context.newPage()
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' })
    await page.goto(`${BASE}/login`)
    await page.fill('input[type=email]', email)
    await page.fill('input[type=password]', password)
    await page.keyboard.press('Enter')
    await page.waitForURL(/\/projects/)
    if (!name) {
      name = `run-notice-${Date.now().toString(36)}`
      const made = await page.evaluate(async (n) => {
        const { default: client } = await import('/src/api/client.ts')
        try {
          await client.post(`/projects/from_template/eh_microgrid_staged?name=${encodeURIComponent(n)}`)
          return 'ok'
        } catch (e) { return String(e?.response?.status ?? e) }
      }, name)
      if (made !== 'ok') throw new Error(`template create: ${made}`)
    }
    scenario = scen
    ended = false
    await page.route(u => u.pathname === '/api/simulation/queue', route => {
      if (route.request().method() !== 'GET') return route.continue()
      const jobs = jobsFor()
      return route.fulfill({ json: { jobs, running: jobs.filter(j => j.status === 'running').map(j => j.id), paused: false } })
    })
    await page.route(u => u.pathname.endsWith('/estimate'), route =>
      route.fulfill({ json: { kind: 'campus', run_ids: [], seconds: 400, samples: 3 } }))
    await page.route(u => u.pathname.includes('log_stream') || u.pathname.endsWith('/log_history'), route => {
      if (route.request().url().includes('log_history')) return route.fulfill({ json: { lines: [], status: 'running' } })
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'retry: 500\n\n' })
    })

    const userId = await page.evaluate(async () => (await (await fetch('/api/auth/me')).json()).id)
    await page.evaluate(({ t, u, away }) => {
      localStorage.setItem('network-diagram:theme', t)
      for (const k of Object.keys(localStorage)) if (k.startsWith('run-notified:') || k.startsWith('run-last-seen:')) localStorage.removeItem(k)
      if (away) localStorage.setItem(`run-last-seen:${u}`, String(Date.now() / 1000 - 3 * 3600))
    }, { t: theme, u: userId, away: scen === 'away' })

    await page.goto(`${BASE}/app?project=${encodeURIComponent(name)}`)
    await page.getByTestId(`ui-mode-${mode}`).click()
    let region
    if (['finished', 'failed', 'interrupted', 'stacked'].includes(scen)) {
      await page.getByTestId('run-status').first().waitFor({ timeout: 20000 })
      await page.waitForTimeout(2000)
      ended = true
      await page.getByTestId('run-notices').waitFor({ timeout: 20000 })
      region = 'notices'
    } else if (scen === 'away') {
      await page.getByTestId('run-notices').waitFor({ timeout: 20000 })
      region = 'notices'
    } else if (scen === 'optin') {
      await page.getByTestId('run-notify-optin').waitFor({ timeout: 20000 })
      region = 'top'
    } else {
      await page.getByTestId('browser-notify-pref').waitFor({ timeout: 20000 })
      region = 'pref'
    }
    if (click) await page.getByTestId(click).first().click()
    await page.waitForTimeout(800)
    const what = region === 'notices'
      ? (await page.getByTestId('run-notices').innerText()).replace(/\s+/g, ' ').trim()
      : region === 'top'
        ? (await page.getByTestId('run-strip').innerText()).replace(/\s+/g, ' ').trim()
        : (await page.getByTestId('browser-notify-pref').innerText()).replace(/\s+/g, ' ').trim()
    let clip
    if (region === 'notices') {
      const b = await page.getByTestId('run-notices').boundingBox()
      clip = { x: Math.max(0, b.x - 24), y: Math.max(0, b.y - 24), width: Math.min(width - Math.max(0, b.x - 24), b.width + 48), height: Math.min(800 - Math.max(0, b.y - 24), b.height + 48) }
    } else if (region === 'top') {
      clip = { x: 0, y: 0, width, height: 120 }
    } else {
      const b = await page.getByTestId('browser-notify-pref').boundingBox()
      clip = { x: 0, y: Math.max(0, b.y - 80), width: Math.min(width, b.x + b.width + 40), height: Math.min(800 - Math.max(0, b.y - 80), b.height + 100) }
    }
    const suffix = theme === 'dark' ? '-dark' : ''
    const file = path.join(out, `run-notice-${shot}-${mode}-${width}${suffix}.png`)
    await page.screenshot({ path: file, clip })
    console.log(`${file} | ${what}`)
    await context.close()
  }
} finally {
  await browser.close()
}
