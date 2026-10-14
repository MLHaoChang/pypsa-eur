// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Native Chromium/SSE/digest pilot smoke with mocked API responses, no model calls.
 * CHROMIUM_BIN=/usr/bin/chromium node scripts/smoke-checked-answers.mjs
 * Real backend numerical/auth guards are covered separately by pytest.
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
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
const entry = `.checked-browser-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'checked-browser-'))
const approvedText = 'StorageUnit Battery: total direct cost 51 EUR. Charging electricity purchases are excluded.'
const unsafe = 'Invented cost 999 EUR and guaranteed profit.'
const receipt = {
  policy_version: '1', content_sha256: createHash('sha256').update(approvedText).digest('hex'),
  evidence_ids: ['browser-evidence'], checked_block_ids: ['total_cost'], limitations: ['Direct costs only.'],
  reviewer_status: 'not_required', request_complete: true,
  request_scope: { profile: 'storage-unit-cost/1', project_id: 'browser-project', entity_id: 'Battery' },
  result_slots: [{ metric: 'total_cost', disposition: 'answered' }],
}
let server, browser, activePage
try {
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import AssistantDock from './components/AssistantDock'
import {useUIStore} from './store/uiStore'
import './index.css'
useUIStore.setState({currentProject:'Demo', projectMismatch:null, assistantDockOpen:true,
  selectedComponent:{type:'StorageUnit',name:'Battery'}})
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{display:'flex',height:'100vh'}}><AssistantDock/></div></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined,
    headless: true, args: ['--no-sandbox'] })
  const page = await browser.newPage({ viewport: { width: 420, height: 850 }, hasTouch: true })
  activePage = page
  page.setDefaultTimeout(10000)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
  const errors = [], requests = [], paths = []
  page.on('pageerror', error => { errors.push(error.message); console.error('BROWSER_PAGE_ERROR:', error.message) })
  let mode = 'pilot'
  let recordMode = 'valid'
  // Match HTTP API paths, never Vite's /src/api/*.ts module imports.
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    paths.push(url.pathname)
    let data = {}
    if (url.pathname.endsWith('/reliability/context')) {
      assert.equal(route.request().headers()['cache-control'], 'no-cache')
      data = { version: 1, project_id: 'browser-project' }
    }
    else if (url.pathname.endsWith('/chat/answers/browser-answer')) {
      assert.equal(route.request().headers()['cache-control'], 'no-cache')
      if (recordMode === 'unavailable') return route.fulfill({ status: 409,
        json: { detail: { error_kind: 'answer_evidence_unavailable' } } })
      data = { version: 1, answer_id: 'browser-answer', turn_id: 'browser-turn',
        project_id: 'browser-project', text: recordMode === 'tampered' ? unsafe : approvedText,
        verdict: 'passed', receipt }
    }
    else if (url.pathname.endsWith('/reliability/capabilities')) data = { version: 1, mode,
      profiles: { 'storage-unit-cost/1': { enabled: true } } }
    else if (url.pathname.endsWith('/chat/profiles')) data = { active_profile_id: 'fixture',
      profiles: [{ id: 'fixture', label: 'Fixture model', model: 'fixture', wire: 'openai', chat_ready: true }] }
    else if (url.pathname.endsWith('/chat/health')) data = { chat_ready: true,
      active_profile: { id: 'fixture', label: 'Fixture model', wire: 'openai' } }
    else if (url.pathname.endsWith('/chat/history')) data = { turns: [], last_session_id: null,
      bound_project: 'Demo', history_gap: 0, pending_turn: null }
    else if (url.pathname.endsWith('/chat/workflows')) data = { workflows: [] }
    else if (url.pathname.endsWith('/uploads')) data = []
    else if (url.pathname.endsWith('/dictation/config')) data = { available: false, languages: ['en'],
      max_bytes: 10485760, max_recording_seconds: 120 }
    else if (url.pathname.endsWith('/network/meta')) data = { name: 'Demo', bus_count: 1, snapshot_count: 3 }
    else if (url.pathname.endsWith('/simulation/status')) data = { running: false, status: 'completed', condition: 'optimal', dispatch: 'fresh' }
    else if (url.pathname.endsWith('/chat/stream')) {
      const body = route.request().postDataJSON()
      requests.push(body)
      if (requests.length === 2) {
        mode = 'disabled'
        return route.fulfill({ status: 503, json: { detail: { error_kind: 'assurance_unavailable', message: 'Pilot disabled.' } } })
      }
      const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
      let stream = frame('session_init', { session_id: 'browser-session', model: 'fixture',
        ...(body.assurance ? { turn_id: 'browser-turn', assurance: { version: 1, mode: 'strict', state: 'checking' } } : {}) })
      if (body.assurance) {
        stream = frame('token', { delta: unsafe }) + frame('thinking', { delta: unsafe }) + stream
        stream += frame('token', { delta: unsafe }) + frame('answer_approved', {
          version: 1, turn_id: 'browser-turn', answer_id: 'browser-answer', verdict: 'passed', receipt,
        }) + frame('token', { delta: approvedText }) + frame('turn_done', { answer_id: 'browser-answer' })
      } else stream += frame('token', { delta: 'An explicitly ordinary reply.' }) + frame('turn_done', {})
      return route.fulfill({ contentType: 'text/event-stream', body: stream })
    }
    await route.fulfill({ json: data })
  })
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`)
  const toggle = page.getByRole('checkbox', { name: 'Check answers', exact: true })
  await page.waitForFunction(() => {
    const toggle = document.querySelector('[data-testid="checked-answer-control"] input')
    return toggle && !toggle.disabled
  }).catch(async error => {
    await fs.writeFile(path.join(artifacts, 'failed-page.html'), await page.content())
    console.error('BROWSER_DEBUG:', JSON.stringify({ paths, text: await page.locator('body').innerText(), artifacts }))
    throw error
  })
  assert.equal(await toggle.isChecked(), false)
  await toggle.focus(); await page.keyboard.press('Space')
  assert.equal(await toggle.isChecked(), true)
  await page.getByTestId('chat-input').fill('Show total cost for storage unit Battery; price year 2026; annual fixed cost fraction 0.5.')
  await page.getByTestId('chat-send').click()
  await page.getByText(approvedText, { exact: true }).waitFor()
  assert.deepEqual(requests[0].assurance, { version: 1, mode: 'strict' })
  assert.deepEqual(requests[0].ui_context.selected_component, { class: 'StorageUnit', name: 'Battery' })
  assert(!(await page.locator('body').innerText()).includes(unsafe))
  await page.getByText('Requested results checked', { exact: true }).waitFor()
  await page.getByTestId('assistant-dock-collapse').click()
  const card = page.getByTestId('companion-outcome')
  await card.getByText(approvedText, { exact: true }).waitFor()
  assert.equal(await card.getAttribute('data-answer-id'), 'browser-answer')
  assert.equal(await card.getAttribute('data-answer-sha256'), receipt.content_sha256)
  await page.setViewportSize({ width: 320, height: 600 })
  const bounds = await card.boundingBox()
  assert(bounds.x >= 0 && bounds.x + bounds.width <= 320, 'Card overflows narrow viewport')
  const companion = await page.getByTestId('companion-launcher').boundingBox()
  assert(companion.y >= 0 && companion.y + companion.height <= 600, 'Companion overflows short viewport')
  await card.getByRole('button', { name: 'Expand answer', exact: true }).tap()
  assert.equal(await card.getByTestId('companion-answer-text').innerText(), approvedText)
  await page.screenshot({ path: path.join(artifacts, 'closed-answer.png'), fullPage: true })
  recordMode = 'tampered'
  await card.getByRole('button', { name: 'Refresh evidence', exact: true }).click()
  await card.getByText('Checked answer unavailable', { exact: true }).waitFor()
  assert(!(await card.innerText()).includes(unsafe))
  assert(!(await card.innerText()).includes(approvedText))
  assert.equal(await card.getByRole('link', { name: 'View answer record', exact: true }).count(), 0)
  recordMode = 'unavailable'
  const refusedRecord = page.waitForResponse(response => response.url().includes('/chat/answers/browser-answer') && response.status() === 409)
  await card.getByRole('button', { name: 'Refresh evidence', exact: true }).click()
  await refusedRecord
  await card.getByText('Checked answer unavailable', { exact: true }).waitFor()
  recordMode = 'valid'
  await card.getByRole('button', { name: 'Refresh evidence', exact: true }).click()
  await card.getByText(approvedText, { exact: true }).waitFor()
  await page.setViewportSize({ width: 420, height: 850 })
  await card.getByRole('button', { name: 'Open transcript', exact: true }).click()
  assert.equal(requests.length, 1, 'Opening transcript submitted a new request')
  await page.screenshot({ path: path.join(artifacts, 'checked-answer.png'), fullPage: true })
  await page.getByTestId('chat-input').fill('A checked follow-up.')
  await page.getByTestId('chat-send').click()
  await page.getByText('Pilot disabled.', { exact: true }).waitFor()
  assert.equal(await toggle.isChecked(), true)
  await page.getByText('Answer checks are not enabled on this server.', { exact: true }).waitFor()
  await page.getByTestId('chat-input').fill('Preserve this draft.')
  await page.getByTestId('chat-send').click()
  assert.equal(requests.length, 2)
  assert.equal(await page.getByTestId('chat-input').inputValue(), 'Preserve this draft.')
  await toggle.uncheck(); await page.getByTestId('chat-send').click()
  await page.getByText('An explicitly ordinary reply.', { exact: true }).waitFor()
  assert.equal(requests.length, 3)
  assert.equal(requests[2].assurance, undefined)
  assert.deepEqual(errors, [])
  const result = { passed: true, browser: 'native Chromium', api: 'mocked', model_calls: 0,
    keyboard_opt_in: true, sse_and_native_digest: true, unsafe_output_withheld: true,
    rollback_preserves_choice_and_draft: true, explicit_opt_out_only: true, requests: requests.length,
    closed_canonical_answer: true, checked_record_revalidated: true, tampered_and_unavailable_withheld: true,
    narrow_touch_reduced_motion: true, artifacts }
  await fs.writeFile(path.join(artifacts, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
} catch (error) {
  if (activePage) {
    await fs.writeFile(path.join(artifacts, 'failed.html'), await activePage.content())
    await activePage.screenshot({ path: path.join(artifacts, 'failed.png'), fullPage: true })
  }
  console.error(JSON.stringify({ artifacts }))
  throw error
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
