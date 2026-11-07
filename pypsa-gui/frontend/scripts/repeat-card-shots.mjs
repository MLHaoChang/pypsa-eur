// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for assurance issue 04 S5: the confirmation card
 * that repeats a held job call, and the same card with a malformed repeat
 * block (alert, Approve disabled). Component-level harness, no API calls.
 * node scripts/repeat-card-shots.mjs [out-dir]   (default: a new temp dir)
 */
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
const entry = `.repeat-card-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'repeat-card-shots-'))
let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConfirmationCard} from './components/ConfirmationCard'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import './index.css'
const q = new URLSearchParams(location.search)
const mode = q.get('mode') === 'expert' ? 'expert' : 'guided'
const openedAt = new Date(2026, 10, 6, 14, 2).getTime() / 1000
const repeat_card = q.get('card') === 'malformed'
  ? { hold_id: 'h1', opened: 'later', opened_at: openedAt }
  : { hold_id: 'h1', fingerprint: 'f', tool_use_id: 't', opened: 'tool_timeout', opened_at: openedAt, job_id: null }
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: mode } as any)
useChatStore.setState({ sessionId: 'shot-session', pending: {
  tool_use_id: 'call-1', tool_name: 'solve_queue_enqueue', args: { project_id: 'Demo' },
  safety_tier: 'execution', confirmation_token: 'token-1', ttl_seconds: 300,
  expires_at_epoch_ms: Date.now() + 300_000, repeat_card } } as any)
createRoot(document.getElementById('root')!).render(<div style={{width: 400, padding: 8}}><ConfirmationCard/></div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const card of ['repeat', 'malformed']) {
    for (const mode of ['guided', 'expert']) {
      const page = await browser.newPage({ viewport: { width: 420, height: 700 }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
      await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({ json: {} }))
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}&card=${card}`)
      await page.getByTestId('chat-confirm-approve').waitFor()
      await page.waitForTimeout(300)
      const file = path.join(out, `aa04-s5-${card}-card-${mode}.png`)
      const box = await page.evaluate(() => document.getElementById('root').firstElementChild.getBoundingClientRect().bottom)
      await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 420, height: Math.ceil(box) + 8 } })
      console.log(file)
      await page.close()
    }
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
