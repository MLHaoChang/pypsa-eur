// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for assurance issue 04 (S1b on): every chat tool-line
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
import {refusalDetail, toolOutcomeLine} from './components/toolOutcome'
import './index.css'
const mode = new URLSearchParams(location.search).get('mode') === 'expert' ? 'expert' : 'guided'
const t0 = new Date(2026, 10, 5, 14, 2).getTime()
const messages: any[] = [{ id: 'u', role: 'user', content: 'Solve the network and check each job.', ts: t0 }]
const add = (id: string, tool: string, outcome: string, job: string | null, minute: number, reason: string | null = null,
  counts: { requested: number; done: number; extra?: number } | null = null) => {
  const ts = t0 + minute * 60000
  messages.push({ id: id + '-req', role: 'tool', content: '→ ' + tool, tool_use_id: id, tool_name: tool, ts })
  messages.push({ id, role: 'tool', content: toolOutcomeLine(tool, { outcome, job_id: job, reason, counts }), tool_use_id: id, tool_name: tool, ts,
    origin_project: 'Demo', tool_result: { _outcome: { version: 1, outcome, job_id: job, reason, counts } } })
}
;['submitted', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled', 'partial', 'uncertain']
  .forEach((o, i) => add('c' + i, 'run_simulation', o, 'fg:' + i, 0))
// A running solve later read as done by wait_for_job: the earlier line follows the later read.
add('s1', 'run_simulation', 'running', 'fg:live', 1)
add('s2', 'wait_for_job', 'completed', 'fg:live', 7)
// A queue job the backend cannot check yet.
add('q1', 'wait_for_job', 'uncertain', 'ad:7', 8, 'adapter_pending')
// Issue 04 S2: component writes read back requested versus actual.
add('b1', 'bulk_update_components', 'completed', null, 9, null, { requested: 5, done: 5 })
add('b2', 'bulk_update_components', 'partial', null, 9, null, { requested: 5, done: 3 })
add('b3', 'batch_create_components', 'failed', null, 9, null, { requested: 2, done: 0 })
add('b4', 'delete_component', 'uncertain', null, 9, 'revision_moved')
// Issue 04 S3: undo, network import and export read back against their snapshot or file.
add('u1', 'undo_last', 'completed', null, 10)
add('u2', 'undo_last', 'uncertain', null, 10, 'entry_moved')
add('i1', 'import_network_nc', 'completed', null, 11, null, { requested: 17, done: 17 })
add('i2', 'import_csv_bundle', 'partial', null, 11, 'rows_missing', { requested: 17, done: 15 })
add('i3', 'import_network_nc', 'partial', null, 11, 'extra_rows', { requested: 17, done: 17 })
add('i4', 'import_excel', 'uncertain', null, 11, 'adapter_pending')
add('e1', 'export_network_nc', 'completed', null, 12)
add('e2', 'export_csv_bundle', 'uncertain', null, 12, 'not_as_network')
// Issue 04 S4a: refusals before any effect (the tool_error frame's \`_outcome\`), and a read in another project.
const deny = (id: string, tool: string, reason: string, detail: string | null = null) => {
  add(id, tool, 'denied', null, 13, reason)
  if (detail) messages[messages.length - 1].content += '\\n' + detail
}
deny('d1', 'delete_component', 'confirmation_denied')
deny('d2', 'batch_delete_components', 'confirmation_expired')
deny('d3', 'bulk_update_components', 'confirmation_revoked')
deny('d4', 'batch_create_components', 'required_guard_failed', 'batch_create_components did not run: components[0]: missing required name.')
deny('d5', 'bulk_update_components', 'confirmation_args_changed', "The action's arguments changed after its confirmation; request and review it again.")
deny('d6', 'bulk_update_components', 'no_progress_stop')
deny('d7', 'delete_component', 'aborted')
deny('d8', 'delete_component', 'user_restriction', 'A rule you set for this project blocks changes.')
deny('d9', 'delete_component', 'approval_unavailable', 'The app could not save the approval: its data folder may be full or not writable.')
deny('d10', 'bulk_update_components', 'round_change_cap')
deny('d11', 'delete_component', 'unknown_decision')
add('o1', 'run_simulation', 'uncertain', 'fg:9', 14, 'other_context')
// Issue 04 S4b: the edit lock before anything ran, extra edits, the raise path and timeouts.
deny('l1', 'bulk_update_components', 'project_locked', 'This project is being edited by another user, so it was not run. Their edit lock must expire or be released first.')
deny('l2', 'delete_component', 'project_lock_unavailable', "Could not verify this project's edit lock, so it was not run. Retry in a moment.")
add('x1', 'bulk_update_components', 'partial', null, 15, 'extra_edits', { requested: 1, done: 1, extra: 2 })
add('x2', 'bulk_update_components', 'partial', null, 15, 'extra_edits', { requested: 5, done: 3, extra: 2 })
add('x3', 'export_network_nc', 'partial', null, 15, 'extra_edits')
const raised = (id: string, tool: string, outcome: string, reason: string | null, detail: string) => {
  add(id, tool, outcome, null, 16, reason)
  messages[messages.length - 1].content += '\\n' + detail
}
raised('r1', 'bulk_update_components', 'failed', null, 'Generator G9 not found')
raised('r2', 'undo_last', 'uncertain', 'changed_before_error', 'the undo snapshot could not be saved')
raised('r3', 'bulk_update_components', 'uncertain', 'tool_timeout', 'It ran longer than 30 s and was stopped; it may have changed the project.')
raised('r4', 'delete_component', 'uncertain', 'tool_not_started', 'It never started: all 8 tool slots were busy for 30 s, so it was cancelled rather than queued.')
add('r5', 'bulk_update_components', 'uncertain', null, 16, 'not_as_requested', { requested: 1, done: 0 })
// Issue 04 S5: a job call held while an earlier one's outcome is unknown (Details in the panel's own words).
deny('h1', 'solve_queue_enqueue', 'operation_pending', refusalDetail('', 'operation_pending'))
deny('h2', 'run_simulation', 'operation_conflict', refusalDetail('', 'operation_conflict'))
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: mode } as any)
// ?only=s4b keeps the issue 04 S4b states (l*, x*, r*), ?only=s5 the S5 holds (h*), so one shot per mode shows them all.
const only = new URLSearchParams(location.search).get('only')
const keep = only === 's4b' ? /^[lxr]\\d/ : only === 's5' ? /^h\\d/ : null
const shown = keep ? messages.filter(m => m.role === 'user' || keep.test(m.tool_use_id ?? '')) : messages
useChatStore.setState({ sessionId: 'shot-session', messages: shown, streaming: false, pending: null, error: null } as any)
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{display:'flex',height:'100vh'}}><ChatPanel/></div></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const [mode, only] of [['guided', ''], ['expert', ''], ['guided', 's4b'], ['expert', 's4b'], ['guided', 's5'], ['expert', 's5']]) {
    {
      const page = await browser.newPage({ viewport: { width: 420, height: 4400 }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
      await page.route(url => url.pathname.startsWith('/api/'), route => {
        const p = new URL(route.request().url()).pathname
        if (p.endsWith('/chat/history')) return route.fulfill({ json: { turns: [], last_session_id: 'shot-session',
          bound_project: 'Demo', history_gap: 0, pending_turn: null } })
        return route.fulfill({ json: {} })
      })
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}${only ? '&only=' + only : ''}`)
      await page.getByText('Solve the network and check each job.').waitFor()
      // Open the refusal messages' Details (issue 04 S4a) so the shot shows them.
      // S5 (UX (12)): Guided collapsed, Expert with Details open.
      if (!(only === 's5' && mode === 'guided')) await page.evaluate(() => document.querySelectorAll('[data-testid=chat-tool-details]').forEach(d => {
        if (/did not run|arguments changed|rule you set|could not save|was not run|not found|ran longer|never started|snapshot|still be running/.test(d.textContent ?? '')) d.open = true
      }))
      await page.waitForTimeout(500)
      const file = path.join(out, only ? `aa04-${only}-tool-outcome-${mode}.png` : `tool-outcome-${mode}.png`)
      if (only) {
        // Every row of the view fits in the panel; crop below the last one (all states, no empty panel).
        const bottom = await page.evaluate(() => Math.max(...[...document.querySelectorAll('[data-testid=chat-message]')]
          .map(e => e.getBoundingClientRect().bottom)))
        await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 420, height: Math.ceil(bottom) + 16 } })
      } else {
        await page.screenshot({ path: file, fullPage: true })
      }
      console.log(file)
      if (mode === 'guided' && !only) {
        // Issue 04 S4a: the rule-blocked line with its Access and approvals link.
        const row = page.getByTestId('chat-message').filter({ has: page.getByTestId('chat-tool-access-link') })
        const linkShot = path.join(out, 'user-restriction-link.png')
        await row.screenshot({ path: linkShot })
        console.log(linkShot)
      }
      await page.close()
    }
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
