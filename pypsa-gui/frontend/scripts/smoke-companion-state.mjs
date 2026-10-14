// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Native Chromium companion state/accessibility smoke, mocked HTTP/SSE only.
 * CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-companion-state.mjs
 * This is offline UI evidence, not a hosted model/media or backend authority probe.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entry = `.companion-browser-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'companion-browser-'))
const scenarios = [
  { name: 'keyboard', viewport: { width: 1000, height: 850 } },
  { name: 'touch-narrow', viewport: { width: 320, height: 850 }, hasTouch: true, isMobile: true },
  { name: 'reduced-motion', viewport: { width: 420, height: 850 }, reducedMotion: 'reduce' },
]
const results = []
let server, browser
try {
  await fs.writeFile(html, '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    + `<body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import AssistantDock from './components/AssistantDock'
import {useUIStore} from './store/uiStore'
import './index.css'
useUIStore.setState({currentProject:'Demo', projectMismatch:null, assistantDockOpen:false})
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <main style={{display:'flex',height:'100vh'}}><div style={{flex:1}}>Engineering canvas</div><AssistantDock/></main>
</QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined,
    headless: true, args: ['--no-sandbox'] })
  for (const scenario of scenarios) {
    const { name, ...options } = scenario
    const context = await browser.newContext(options)
    await context.addInitScript(() => {
      window.__mediaCalls = { capture: 0, playback: 0, rtc: 0 }
      navigator.mediaDevices.getUserMedia = async () => {
        window.__mediaCalls.capture++
        throw new DOMException('Denied in this fixture', 'NotAllowedError')
      }
      speechSynthesis.speak = () => { window.__mediaCalls.playback++ }
      const RTC = window.RTCPeerConnection
      window.RTCPeerConnection = class extends RTC {
        constructor(...args) { super(...args); window.__mediaCalls.rtc++ }
      }
    })
    // All application APIs are intercepted; no remote provider calls are allowed.
    await context.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
    const page = await context.newPage()
    const errors = [], requests = [], controls = []
    page.on('pageerror', error => errors.push(error.message))
    let releaseRead
    let pendingRead
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const pathname = new URL(route.request().url()).pathname
      let data = {}
      if (pathname.endsWith('/chat/profiles')) data = { active_profile_id: 'fixture',
        profiles: [{ id: 'fixture', label: 'Fixture model', model: 'fixture', wire: 'openai', chat_ready: true }] }
      else if (pathname.endsWith('/chat/health')) data = { chat_ready: true,
        active_profile: { id: 'fixture', label: 'Fixture model', wire: 'openai' } }
      else if (pathname.endsWith('/chat/history')) data = { turns: [], last_session_id: null,
        bound_project: 'Demo', history_gap: 0, pending_turn: null }
      else if (pathname.endsWith('/reliability/capabilities')) data = { version: 1, mode: 'disabled', profiles: {} }
      else if (pathname.endsWith('/chat/workflows')) data = { workflows: [] }
      else if (pathname.endsWith('/uploads')) data = []
      else if (pathname.endsWith('/dictation/config')) data = { available: false, languages: ['en'],
        max_bytes: 10485760, max_recording_seconds: 120 }
      else if (pathname.endsWith('/network/meta')) data = { name: 'Demo', bus_count: 1, snapshot_count: 2 }
      else if (pathname.endsWith('/simulation/status')) data = { running: true, status: 'running', dispatch: 'none' }
      else if (pathname.includes('/durable-conversations')) data = { thread_id: 'fixture-thread', generation: 1 }
      else if (pathname.includes('/abort') || pathname.includes('/confirm') || pathname.includes('/cancel')) {
        controls.push(pathname)
      } else if (pathname.endsWith('/chat/stream')) {
        requests.push(route.request().postDataJSON())
        const frame = (event, payload) => `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
        let body = frame('session_init', { session_id: 'fixture-session', model: 'fixture' })
        if (requests.length === 1) {
          pendingRead = route
          releaseRead = () => route.fulfill({ contentType: 'text/event-stream', body: body
            + frame('token', { delta: 'A fixture response.' }) + frame('turn_done', {}) })
          return
        }
        body += frame('tool_pending_confirmation', { tool_use_id: 'solve-1', tool_name: 'run_simulation',
          args: {}, safety_tier: 'execute', confirmation_token: 'fixture-token', ttl_seconds: 600 })
        return route.fulfill({ contentType: 'text/event-stream', body })
      }
      await route.fulfill({ json: data })
    })
    try {
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`)
      const chat = page.getByRole('button', { name: 'Chat here', exact: true })
      await chat.waitFor()
      const actions = page.getByTestId('companion-actions')
      assert.equal(await actions.evaluate(el => getComputedStyle(el).opacity), '1', 'Actions require hover')
      assert.equal(await page.getByRole('button', { name: 'Start voice', exact: true }).isDisabled(), true)
      await page.getByText('Continuous voice is not connected. Use reviewed dictation for speech.', { exact: true }).waitFor()
      if (options.hasTouch) await chat.tap()
      else { await chat.focus(); await page.keyboard.press('Enter') }
      const input = page.getByRole('textbox', { name: 'Message the assistant', exact: true })
      assert.equal(await input.evaluate(el => el === document.activeElement), true)
      await page.waitForFunction(() => document.querySelector('[data-testid="chat-model-select"]')?.getAttribute('data-profiles-state') === 'ready')
      await input.fill('List the generators')
      await input.press('Enter')
      await page.getByText('Response in progress', { exact: true }).waitFor()
      await page.waitForFunction(() => document.querySelector('[data-testid="companion-launcher"]')?.getAttribute('data-work-state') === 'running')
      assert.equal(requests.length, 1)
      assert.equal(requests[0].input_mode, 'text')
      assert.equal(requests[0].generation, 1)
      assert.equal(typeof requests[0].utterance_id, 'string')
      // Read completion is explicitly controlled rather than inferred from timing.
      assert(pendingRead && releaseRead)
      await releaseRead()
      await page.getByText('Ready for a message', { exact: true }).waitFor()
      const launcher = page.getByTestId('companion-launcher')
      assert.equal(await launcher.getAttribute('data-media-state'), 'off')
      assert.equal(await launcher.getAttribute('data-presence'), 'idle')
      const bounds = await launcher.boundingBox()
      assert(bounds.x >= 0 && bounds.x + bounds.width <= options.viewport.width, 'Companion overflows viewport')
      if (options.reducedMotion === 'reduce') {
        assert.equal(await page.getByTestId('companion-character').evaluate(el => getComputedStyle(el).animationName), 'none')
      }
      await input.fill('Run a simulation')
      await input.press('Enter')
      await page.getByText('Needs approval', { exact: true }).waitFor()
      await page.getByRole('button', { name: 'Close chat here', exact: true }).click()
      assert.equal(await page.getByTestId('companion-confirmation').isVisible(), true)
      assert.equal(await page.getByTestId('companion-work-status').innerText(), 'Needs approval')
      assert.deepEqual(controls, [], 'Closing text chat submitted a work control')
      assert.deepEqual(await page.evaluate(() => window.__mediaCalls), { capture: 0, playback: 0, rtc: 0 })
      assert.deepEqual(errors, [])
      await page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true })
      results.push({ scenario: name, passed: true, stream_requests: requests.length })
    } catch (error) {
      await fs.writeFile(path.join(artifacts, `${name}-failed.html`), await page.content())
      console.error(JSON.stringify({ scenario: name, artifacts, errors }))
      throw error
    } finally {
      await context.close()
    }
  }
  console.log(JSON.stringify({ passed: true, browser: 'native Chromium', api: 'mocked HTTP/SSE',
    model_calls: 0, scenarios: results, artifacts }))
} finally {
  await browser?.close()
  await server?.close()
  await fs.rm(html, { force: true })
  await fs.rm(source, { force: true })
}
