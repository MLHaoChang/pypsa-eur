// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Homer-parity 04a screenshot harness: the chat edit card for a rate-only load-growth edit
 * (update_solver_config, electrical 2 -> 3 % per year; stored start year 2030 stays), Guided and Expert, light theme.
 * The card JSON is the backend's own output (harness.edit_card.preview, as in
 * tests/test_edit_card.py::test_solver_card_shows_the_stored_start_year_beside_a_rate_only_change), passed in as a
 * file; every /api route and the chat stream are mocked in the browser, so no backend or sign-in is needed.
 *
 * Needs Vite on :5173 (started here when nothing answers) and a health stub on :8000 (as load-growth-shots.mjs).
 * Usage: CARD_JSON=<card.json> node scripts/load-growth-card-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'homer-parity', 'shots-04a'))
const PROJECT = 'Load growth shots'
const CARD = JSON.parse(await fs.readFile(process.env.CARD_JSON, 'utf8'))

async function ensureStub() {
  if (await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}
async function ensureVite() {
  if (await fetch(BASE).then(r => r.ok).catch(() => false)) return null
  const p = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: frontend, stdio: 'ignore' })
  for (let i = 0; i < 90; i++) { if (await fetch(BASE).then(r => r.ok).catch(() => false)) return p; await new Promise(r => setTimeout(r, 1000)) }
  p.kill(); throw new Error('Vite did not come up on :5173')
}

await fs.mkdir(OUT, { recursive: true })
const problems = [], written = []
let stub = null, vite = null, browser = null
try {
  stub = await ensureStub()
  vite = await ensureVite()
  browser = await chromium.launch()
  for (const mode of ['guided', 'expert']) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:assistant-dock', 'open')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
      localStorage.setItem('network-diagram:theme-schema', '2')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${(e.stack || e.message).split('\n').slice(0, 4).join(' / ')}`))
    const frame = (event, payload) => `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p.endsWith('/chat/profiles')) return ok({ active_profile_id: 'fixture', profiles: [{ id: 'fixture', label: 'Fixture model', model: 'fixture', wire: 'openai', chat_ready: true }] })
      if (p.endsWith('/chat/health')) return ok({ chat_ready: true, active_profile: { id: 'fixture', label: 'Fixture model', wire: 'openai' } })
      if (p.endsWith('/chat/history')) return ok({ turns: [], last_session_id: null, bound_project: PROJECT, history_gap: 0, pending_turn: null })
      if (p.endsWith('/reliability/capabilities')) return ok({ version: 1, mode: 'disabled', profiles: {} })
      if (p.endsWith('/chat/workflows')) return ok({ workflows: [] })
      if (p.endsWith('/dictation/config')) return ok({ available: false, languages: ['en'], max_bytes: 10485760, max_recording_seconds: 120 })
      if (p.includes('/durable-conversations')) return ok({ thread_id: 'fixture-thread', generation: 1 })
      if (p.endsWith('/chat/stream')) {
        return r.fulfill({ contentType: 'text/event-stream', body: frame('session_init', { session_id: 'fixture-session', model: 'fixture' })
          + frame('token', { delta: 'I will raise the electrical load growth to 3 % per year.' })
          + frame('tool_pending_confirmation', { tool_use_id: 'edit-1', tool_name: 'update_solver_config',
            args: { partial: { load_growth_pct_per_year: { electrical: 3.0 } } }, safety_tier: 'edit',
            confirmation_token: 'fixture-token', ttl_seconds: 600, edit_card: CARD }) })
      }
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return ok({})
      if (p === '/api/network/meta') return ok({ name: PROJECT, bus_count: 2, snapshot_count: 3, line_count: 1, generator_count: 1, load_count: 1, storage_unit_count: 0, link_count: 0, transformer_count: 0, store_count: 0 })
      if (p === '/api/simulation/status') return ok({ running: false, status: 'idle' })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 3, has_results: false, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
      if (p.endsWith('/sites') && m === 'GET') return ok({ version: 1, asset_footprints: {}, sites: [] })
      if (p.endsWith('/context') || p.endsWith('/flow/scope')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    try {
      await page.goto(`${BASE}/`)
      await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
      const input = page.getByTestId('chat-input')
      await input.waitFor()
      await page.waitForTimeout(800)
      await input.fill('Raise electrical load growth to 3 percent per year')
      await input.press('Enter')
      const card = page.getByTestId('chat-edit-card').first()
      await card.waitFor()
      await page.waitForTimeout(500)
      await card.scrollIntoViewIfNeeded()
      console.log(`--- ${mode} card text:\n${await card.innerText()}`)
      const file = path.join(OUT, `chat-card-rate-only-${mode}.png`)
      await page.getByTestId('chat-panel').screenshot({ path: file })
      written.push(path.basename(file))
    } catch (e) {
      problems.push(`${mode}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-card-${mode}.png`) }).catch(() => {})
    }
    await ctx.close()
  }
} finally { await browser?.close(); stub?.close(); vite?.kill() }
console.log(`wrote ${written.length}: ${OUT}\n${written.join(', ')}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
