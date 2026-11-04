// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * LC-07 PR3 screenshot harness: the Companion Live composer with continuous speech, Guided and Expert.
 * Every /api route is mocked in the browser (no backend or sign-in), the microphone is a fake stream and
 * the media link is a no-op installed through `configureSpeechMediaLink` (no SDK ships).
 *
 * Needs Vite on :5173. Vite's auth gate asks :8000 for /api/health, so a health-only stub starts there
 * unless something already answers.
 *
 * States: `1-off` (Start voice beside Send), `2-listening`, `3-stopped-hidden` (tab hidden: track stopped,
 * reason line), `4-unavailable` (server flag off: master's voice note), `5-no-link` (flag on, no media link:
 * a disabled Start voice beside Send, the reason in the composer caption).
 *
 * Usage: node scripts/live-speech-shots.mjs [outDir]      env: BASE, DEBUG=1 (unmocked routes, console errors)
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'live-copilot', 'evidence', '07'))
const SCHEMAS = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))
const PROJECT = 'Live speech shots'
const THREAD = { thread_id: 't-shot', generation: 1 }

async function ensureStub() {
  const up = await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)
  if (up) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}

await fs.mkdir(OUT, { recursive: true })
const stub = await ensureStub()
const browser = await chromium.launch()
const problems = [], written = []
try {
  for (const mode of ['guided', 'expert']) for (const variant of ['on', 'off', 'no-link']) {
    const flagOn = variant !== 'off'
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
      // A fake microphone: the page counts stopped tracks so the shot can prove the release.
      window.__tracksStopped = 0
      const track = { kind: 'audio', onended: null, stop() { window.__tracksStopped += 1 } }
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
        getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }),
        enumerateDevices: async () => [], addEventListener() {}, removeEventListener() {},
      } })
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    if (process.env.DEBUG) page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) console.log(mode, 'console:', m.text().slice(0, 1200).replace(/\n/g, ' / ')) })
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message.split('\n')[0]}`))
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      // The Expert chat greeting reads the network meta; {} would crash it and remount the dock.
      if (p === '/api/network/meta') return ok({ name: PROJECT, bus_count: 0, snapshot_count: 24 })
      if (p.endsWith('/flow/reliability_goal')) return ok({ target_lole_h: { value: null, source: 'default' }, ens_cap_permyriad: { value: null, source: 'default' },
        mc_draws: { value: 200, source: 'default' }, site_archetype: { value: 'strong_grid', source: 'default' } })
      if (p.endsWith('/sites') && m === 'GET') return ok({ version: 1, asset_footprints: {}, sites: [] })
      if (/\/grid-readiness$/.test(p)) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
      const sch = p.match(/^\/api\/network\/asset_schema\/([^/]+)$/)
      if (sch) return SCHEMAS[sch[1]] ? ok(SCHEMAS[sch[1]]) : r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 24, has_results: false, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (p === '/api/durable-conversations' && m === 'POST') return ok(THREAD)
      if (p.endsWith('/speech') && m === 'GET') return flagOn ? ok({ generation: 1, session: null })
        : r.fulfill({ status: 503, json: { detail: { error_kind: 'speech_disabled', message: 'Continuous speech is not enabled on this server. Type your message instead.' } } })
      if (p.endsWith('/speech/start')) return ok({ session: { session_id: 'sp-shot', state: 'open', expires_at: new Date(Date.now() + 600_000).toISOString() },
        generation: 1, media: { url: 'wss://media.invalid/room', token: 'shot-token', expires_at: Date.now() / 1000 + 300 }, reconcile: [] })
      if (p.endsWith('/speech/end')) return ok({ status: 'closed', session: null })
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      if (process.env.DEBUG) console.log('unmocked', m, p)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    if (variant !== 'no-link') await page.evaluate(async () => {
      const { configureSpeechMediaLink } = await import('/src/utils/liveSpeechSession.ts')
      configureSpeechMediaLink(() => ({ connect: async () => {}, close() {} }))
    })
    await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
    const shot = async (key) => {
      const file = path.join(OUT, `${mode}-${key}.png`)
      await page.waitForTimeout(400)
      await page.screenshot({ path: file }); written.push(path.basename(file))
    }
    const step = async (key, fn) => { try { await fn() } catch (e) {
      problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
      if (process.env.DEBUG) {
        await page.screenshot({ path: path.join(OUT, `debug-fail-${mode}-${key}.png`) })
        console.log(key, await page.evaluate(async () => {
          const c = (await import('/src/store/chatStore.ts')).useChatStore.getState()
          return JSON.stringify({ t: c.durableThreadId, g: c.utteranceGeneration, composer: !!document.querySelector('[data-testid=companion-live-composer]'), live: document.querySelector('[data-testid=companion-launcher]')?.getAttribute('data-live') })
        }))
      }
    } }
    // Open "Chat here" and keep it open: the shell may remount once while the project settles, which
    // resets the in-page chat, so re-open until the composer stays (dispatch: the launcher still moves).
    for (let i = 0; i < 6; i++) {
      await page.waitForTimeout(1000)
      if ((await page.getByTestId('companion-launcher').getAttribute('data-live')) !== 'open') await page.getByTestId('companion-live').dispatchEvent('click')
      await page.waitForTimeout(1500)
      if ((await page.getByTestId('companion-launcher').getAttribute('data-live')) === 'open') break
    }
    await page.getByTestId('companion-live-composer').waitFor()
    const speak = page.getByTestId('companion-voice-toggle')
    const note = page.getByTestId('companion-voice-note')
    const caption = page.getByTestId('companion-live-caption')
    if (variant === 'off') {
      await step('4-unavailable', async () => {
        await page.waitForTimeout(800)
        if (await speak.count()) throw new Error('Start voice is in the composer while the server flag is off')
        if (!/not connected/.test(await note.textContent())) throw new Error('the master voice note changed')
        await shot('4-unavailable')
      })
      await ctx.close(); continue
    }
    if (variant === 'no-link') {
      await step('5-no-link', async () => {
        await speak.waitFor()
        if (!(await speak.isDisabled())) throw new Error('Start voice is enabled without a media link')
        if (await note.count()) throw new Error('the voice note shows while the flag is on')
        if (!/no media connection set up/.test(await caption.textContent())) throw new Error('the reason is not visible')
        await shot('5-no-link')
      })
      await ctx.close(); continue
    }
    await step('1-off', async () => { await speak.waitFor(); await shot('1-off') })
    await step('2-listening', async () => {
      await speak.dispatchEvent('click')
      await speak.filter({ hasText: 'Stop voice' }).waitFor()
      const media = await page.getByTestId('companion-launcher').getAttribute('data-media-state')
      if (media !== 'live') throw new Error(`data-media-state is ${media}`)
      await shot('2-listening')
    })
    await step('3-stopped-hidden', async () => {
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
        document.dispatchEvent(new Event('visibilitychange'))
        Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
      })
      await speak.filter({ hasText: 'Start voice' }).waitFor()
      const stopped = await page.evaluate(() => window.__tracksStopped)
      if (stopped !== 1) throw new Error(`tracks stopped: ${stopped}`)
      await shot('3-stopped-hidden')
    })
    await ctx.close()
  }
} finally { await browser.close(); stub?.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
