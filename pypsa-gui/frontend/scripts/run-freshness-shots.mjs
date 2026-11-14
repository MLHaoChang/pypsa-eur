// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for provenance-graph 04b: the freshness count beside
 * the header's Run button ("(2 out of date)", "(1 unchecked)", both), in Guided
 * and Expert. Drives the running app (API on :8000, Vite on :5173), signs in as
 * the dev admin, opens the first project on /projects, and rewrites the project
 * flow GET's `results.freshness_by_kind` and `run_request`. No model calls.
 * Guided hides the idle Run (the hub design has its own), so its shots show the
 * header with no count: the script checks the count is absent there.
 * node scripts/run-freshness-shots.mjs [out-dir]   (default: a new temp dir)
 */
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.SHOTS_BASE || 'http://127.0.0.1:5173'
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'run-freshness-shots-'))
const STATES = {
  stale: { optimisation: 'stale', study: null, loadflow: 'stale' },
  unchecked: { optimisation: 'uncompared', study: null, loadflow: 'fresh' },
  both: { optimisation: 'uncompared', study: 'stale', loadflow: 'stale' },
}
const EXPECT = { stale: '(2 out of date)', unchecked: '(1 unchecked)', both: '(2 out of date · 1 unchecked)' }
const WIDTH = 1440, HEADER = 120

await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
try {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let state = 'stale'
  await page.route(url => url.pathname.startsWith('/api/projects/') && url.pathname.endsWith('/flow'), async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const res = await route.fetch()
    const flow = await res.json()
    for (const s of flow.sections ?? []) if (s.section === 'results') s.freshness_by_kind = STATES[state]
    flow.run_request = { optimisation: true, study: true, loadflow: true }
    flow.deferred = false
    return route.fulfill({ response: res, json: flow })
  })

  const { email, password } = devCredentials()
  await page.goto(`${BASE}/login`)
  await page.fill('input[type=email]', email)
  await page.fill('input[type=password]', password)
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/projects/)
  const card = page.locator('a[href^="/app?project="]').first()
  await card.waitFor()
  const appUrl = await card.getAttribute('href')

  for (const name of Object.keys(STATES)) {
    state = name
    for (const mode of ['guided', 'expert']) {
      await page.goto(`${BASE}${appUrl}`)
      await page.getByTestId(`ui-mode-${mode}`).click()
      const stale = page.getByTestId('run-stale')
      let text = '(no Run in Guided)'
      if (mode === 'expert') {
        await stale.waitFor()
        text = (await stale.innerText()).trim()
        if (text !== EXPECT[name]) throw new Error(`${name}/${mode}: beside Run reads "${text}", expected "${EXPECT[name]}"`)
      } else {
        await page.getByTestId('ui-mode-switch').waitFor()
        await page.waitForTimeout(3000)
        if (await stale.count()) throw new Error(`${name}/guided: a freshness count shows though Guided hides the idle Run`)
      }
      await page.mouse.move(WIDTH / 2, 600)
      await page.waitForTimeout(500)
      const file = path.join(out, `header-${name}-${mode}.png`)
      await page.screenshot({ path: file, clip: { x: 0, y: 0, width: WIDTH, height: HEADER } })
      console.log(file, text)
      if (name === 'both' && mode === 'expert') {
        // The count's title is a native tooltip; headless Chromium may not paint it.
        await stale.hover()
        await page.waitForTimeout(1500)
        const hover = path.join(out, `header-${name}-${mode}-hover.png`)
        await page.screenshot({ path: hover, clip: { x: 0, y: 0, width: WIDTH, height: HEADER } })
        console.log(hover, 'title:', await stale.getAttribute('title'))
      }
    }
  }
} finally {
  await browser.close()
}
