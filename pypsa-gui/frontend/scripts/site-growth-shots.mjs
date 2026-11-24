// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Homer-parity 04a2 screenshot harness: the generic site form's "Grow from the first stage at [ ] % per year"
 * row (Load group), reached the way a user does: Guided Start card > "Your own site" tile. Every /api route is
 * mocked in the browser (as load-growth-shots.mjs); the form's schema is siteFormFixture.json. The form has no
 * Guided/Expert split, so one mode (Guided, where the Start card lives) is shot. Each state in light and dark,
 * cropped to the Load group.
 *
 * Vite runs on :5174 (started here when nothing answers). The auth gate asks :8000 for /api/health, so a
 * health-only stub is started there unless something already answers.
 *
 * States: empty (2 stages), rate, typed-override, validation-error, one-stage.
 * Usage: node scripts/site-growth-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5174'
const PORT = new URL(BASE).port
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'homer-parity', 'shots-04a2'))
const FORM = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'pages', 'hubDesign', 'siteFormFixture.json'), 'utf8'))

const tid = (page, id) => page.getByTestId(id)
const removeStage = (page, i) => tid(page, `site-stage-remove-${i}`).click()
const STATES = {
  'empty': async page => { await removeStage(page, 2) },
  'rate': async page => { await tid(page, 'site-field-growth_pct').fill('5') },
  'typed-override': async page => {
    await tid(page, 'site-field-growth_pct').fill('5')
    await page.locator('[data-testid="site-stages"] tbody tr').nth(1).locator('input').nth(1).fill('10')
    await tid(page, 'site-stage-reset-1').waitFor()
  },
  'validation-error': async page => {
    await tid(page, 'site-field-growth_pct').fill('-100')
    await page.getByRole('status').filter({ hasText: /\S/ }).first().waitFor()
  },
  'one-stage': async page => { await removeStage(page, 2); await removeStage(page, 1) },
}

async function ensureStub() {
  const up = await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)
  if (up) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}
async function ensureVite() {
  if (await fetch(BASE).then(r => r.ok).catch(() => false)) return null
  const p = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', PORT, '--strictPort'], { cwd: frontend, stdio: 'ignore' })
  for (let i = 0; i < 90; i++) { if (await fetch(BASE).then(r => r.ok).catch(() => false)) return p; await new Promise(r => setTimeout(r, 1000)) }
  p.kill(); throw new Error(`Vite did not come up on :${PORT}`)
}

await fs.mkdir(OUT, { recursive: true })
const problems = [], written = []
let stub = null, vite = null, browser = null
try {
  stub = await ensureStub()
  vite = await ensureVite()
  browser = await chromium.launch()
  for (const theme of ['light', 'dark']) for (const [key, drive] of Object.entries(STATES)) {
    if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2, reducedMotion: 'reduce', colorScheme: theme })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ theme }) => {
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      localStorage.setItem('network-diagram:ui-mode', 'guided')
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
      localStorage.setItem('network-diagram:theme-schema', '2')
    }, { theme })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${theme}/${key}: page error: ${(e.stack || e.message).split('\n').slice(0, 4).join(' / ')}`))
    const unmocked = new Set()
    if (process.env.DEBUG) page.on('console', c => { if (c.type() === 'error') console.log('console:', c.text().slice(0, 600)) })
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p === '/api/projects/from_template/eh_site/form') return ok(FORM)
      if (p === '/api/projects/' || p === '/api/projects') return ok([])
      if (p === '/api/network/snapshots') return ok({ count: 0, snapshots: [], periods: [], freq: 'h', ts_start: null, ts_end: null, can_sample_weeks: false, weightings: [] })
      if (p === '/api/network/investment_periods') return ok({ periods: [], weightings: [] })
      if (p === '/api/simulation/status') return ok({ running: false, status: 'idle' })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p.endsWith('/context') || p.endsWith('/flow/scope') || /\/flow$/.test(p)) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      unmocked.add(`${m} ${p}`)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    try {
      await page.goto(`${BASE}/app`)
      await page.waitForLoadState('networkidle')
      await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
      await page.evaluate(async () => {
        const hub = (await import('/src/pages/hubDesign/hubDesignStore.ts')).useHubDesignStore.getState()
        hub.setStep('start', { user: true })
        ;(await import('/src/store/uiStore.ts')).useUIStore.getState().setSlidePanel('hubDesign')
      })
      await tid(page, 'hub-start-own-site').click()
      await tid(page, 'site-form').waitFor()
      await tid(page, 'site-type-industrial').click()
      await tid(page, 'site-stages').waitFor()
      await drive(page)
      await page.waitForTimeout(400)
      const group = page.locator('fieldset').filter({ has: page.getByText('Load', { exact: true }) }).filter({ has: tid(page, 'site-stages') })
      await group.scrollIntoViewIfNeeded()
      const file = path.join(OUT, `${key}-${theme}.png`)
      await page.evaluate(() => document.activeElement?.blur?.())
      await group.screenshot({ path: file })
      written.push(path.basename(file))
    } catch (e) {
      problems.push(`${theme}/${key}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${key}-${theme}.png`) }).catch(() => {})
    }
    if (process.env.DEBUG) console.log(`${theme}/${key} unmocked:`, [...unmocked].join(' | '))
    await ctx.close()
  }
} finally { await browser?.close(); stub?.close(); vite?.kill() }
console.log(`wrote ${written.length}: ${OUT}\n${written.join(', ')}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
