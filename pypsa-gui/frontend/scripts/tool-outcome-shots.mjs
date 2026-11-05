// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for assurance issue 04 S1b: every chat tool-line
 * outcome mark in Guided and Expert. Component-level harness (the chat panel
 * with a seeded transcript), all API calls mocked, no model calls.
 * node scripts/tool-outcome-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.tool-outcome-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'tool-outcome-shots-'))
let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import ChatPanel from './components/ChatPanel'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import {toolOutcomeLine} from './components/toolOutcome'
import './index.css'
const mode = new URLSearchParams(location.search).get('mode') === 'expert' ? 'expert' : 'guided'
const t0 = new Date(2026, 10, 5, 14, 2).getTime()
const messages: any[] = [{ id: 'u', role: 'user', content: 'Solve the network and check each job.', ts: t0 }]
const add = (id: string, tool: string, outcome: string, job: string | null, minute: number, reason: string | null = null) => {
  const ts = t0 + minute * 60000
  messages.push({ id: id + '-req', role: 'tool', content: '→ ' + tool, tool_use_id: id, tool_name: tool, ts })
  messages.push({ id, role: 'tool', content: toolOutcomeLine(tool, { outcome, job_id: job, reason }), tool_use_id: id, tool_name: tool, ts,
    origin_project: 'Demo', tool_result: { _outcome: { version: 1, outcome, job_id: job, reason } } })
}
;['submitted', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled', 'partial', 'uncertain']
  .forEach((o, i) => add('c' + i, 'run_simulation', o, 'fg:' + i, 0))
// A running solve later read as done by wait_for_job: the earlier line follows the later read.
add('s1', 'run_simulation', 'running', 'fg:live', 1)
add('s2', 'wait_for_job', 'completed', 'fg:live', 7)
// A queue job the backend cannot check yet.
add('q1', 'wait_for_job', 'uncertain', 'ad:7', 8, 'adapter_pending')
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: mode } as any)
useChatStore.setState({ sessionId: 'shot-session', messages, streaming: false, pending: null, error: null } as any)
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{display:'flex',height:'100vh'}}><ChatPanel/></div></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const mode of ['guided', 'expert']) {
    {
      const page = await browser.newPage({ viewport: { width: 420, height: 900 }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
      await page.route(url => url.pathname.startsWith('/api/'), route => {
        const p = new URL(route.request().url()).pathname
        if (p.endsWith('/chat/history')) return route.fulfill({ json: { turns: [], last_session_id: 'shot-session',
          bound_project: 'Demo', history_gap: 0, pending_turn: null } })
        return route.fulfill({ json: {} })
      })
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}`)
      await page.getByText('Solve the network and check each job.').waitFor()
      await page.waitForTimeout(500)
      const file = path.join(out, `tool-outcome-${mode}.png`)
      await page.screenshot({ path: file, fullPage: true })
      console.log(file)
      await page.close()
    }
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
