// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for LC-47 S2: the change-set card (a tick box per
 * proposed change, one approval) and its one receipt, in Guided and Expert,
 * dark and light. Component-level harness (the chat panel with a seeded
 * transcript and card), all API calls mocked, no model calls.
 * node scripts/change-set-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.change-set-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'change-set-shots-'))
let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import ChatPanel from './components/ChatPanel'
import {changeSetItemLabel} from './components/ChangeSetCard'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import './index.css'
const q = new URLSearchParams(location.search)
const mode = q.get('mode') === 'expert' ? 'expert' : 'guided'
const state = q.get('state') ?? 'card'
document.documentElement.dataset.theme = q.get('theme') === 'light' ? 'light' : 'dark'
const t0 = new Date(2026, 10, 10, 14, 2).getTime()
const row = (cls: string, name: string, field: string, label: string, old: any, now: any, unit: string, extra: any = {}) =>
  ({ component_class: cls, name, field, label, old, new: now, unit, role: 'typed', ...extra })
const card = (tool: string, says: string, rows: any[]) =>
  ({ version: 1, kind: 'edit', tool, complete: true, digest: says.length.toString(16).padStart(64, '0'), sets: [], says, rows,
    section: { id: 'connection', label: 'Connection & grid' } })
const site = [
  card('update_component', 'Set Link Grid import eh_role to grid_import',
    [row('Link', 'Grid import', 'eh_role', 'Role at the site', '', 'grid_import', '', { words: { old: 'Not set', new: 'Grid import' } })]),
  card('update_component', 'Set Bus POC eh_poc to True, eh_sk_mva to 2500',
    [row('Bus', 'POC', 'eh_poc', 'Point of connection', false, true, ''),
     row('Bus', 'POC', 'eh_sk_mva', 'Short-circuit power', 0, 2500, 'MVA')]),
  card('update_component', 'Set Bus IT hall eh_critical to True',
    [row('Bus', 'IT hall', 'eh_critical', 'Must stay on', false, true, '')]),
  card('update_component', 'Set Generator Genset A outage_rate_value to 2, mttr_hours to 48',
    [row('Generator', 'Genset A', 'outage_rate_value', 'Outage rate', 0, 2, '1/year'),
     row('Generator', 'Genset A', 'mttr_hours', 'Repair time', 0, 48, 'h')]),
]
const profiles = ['PV north', 'PV south', 'Wind 1'].map((g, i) => card('apply_generated_profile',
  'Apply the generated profile to Generator ' + g,
  [row('Generator', g, 'p_max_pu', 'Availability profile', 'none', '8760 hours, mean ' + [0.14, 0.15, 0.31][i], '',
    { source: { filename: 'ERA5 2019', raw: 'job ' + (i + 1), raw_unit: '' } })]))
const cards = state === 'profiles' ? profiles : site
const items = cards.map((c, i) => ({ tool_use_id: 'tu-' + i, tool: c.tool, edit_card: c,
  source: state === 'profiles' ? 'rows' : 'proposed by the assistant', digest: 'd' + i }))
const change_set = { version: 1, digest: 's'.repeat(64), items, requirements: { checked: 2,
  says: 'These changes were checked together against the project\u2019s 2 confirmed requirements.' } }
const labels = Object.fromEntries(items.map(i => [i.tool_use_id, changeSetItemLabel(i as any)]))
const messages: any[] = [
  { id: 'u', role: 'user', content: 'Fix the gaps on the Site card.', ts: t0 },
  { id: 'a', role: 'assistant', content: 'The site has four gaps. I propose one fix for each; tick the ones to apply.', ts: t0 },
]
const receipt = (decision: string, statuses: [string, string | null][]) => ({ version: 1, decision,
  rows: statuses.map(([status, error_kind], n) => ({ tool_use_id: 'tu-' + n, tool: 'update_component', status, error_kind,
    label: site[n].says })),
  ticked: decision === 'approve' ? statuses.filter(([s]) => s !== 'unticked').length : 0,
  landed: statuses.filter(([s]) => s === 'applied').length,
  failed: statuses.filter(([s]) => s === 'failed' || s === 'stale').length, all_applied: false })
const settle = (r: any, reasons: any = {}) => messages.push({ id: 'r', role: 'tool', content: 'receipt', ts: t0 + 60000,
  change_set_receipt: { receipt: r, labels, reasons } })
if (state === 'partial') settle(receipt('approve', [['applied', null], ['failed', 'validation_error'], ['unticked', 'change_unticked'], ['applied', null]]),
  { 'tu-1': 'Short-circuit power must be above the import capacity of 3000 MVA' })
if (state === 'all') settle(receipt('approve', [['applied', null], ['applied', null], ['uncertain', null], ['stale', 'edit_preview_stale']]))
if (state === 'notrun') settle(receipt('approve', [['applied', null], ['applied', null], ['not_run', 'tool_call_cap_exceeded'], ['not_run', null]]))
if (state === 'cancelled') settle(receipt('deny', [['failed', null], ['failed', null], ['failed', null], ['failed', null]]))
if (state === 'timeout') settle(receipt('expired', [['failed', null], ['failed', null], ['failed', null], ['failed', null]]))
if (state === 'stopped') settle(receipt('aborted', []))
const open = ['card', 'profiles', 'malformed'].includes(state)
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: mode } as any)
useChatStore.setState({ sessionId: 'shot-session', messages, streaming: false, error: null, pending: open ? {
  tool_use_id: 'tu-0', tool_name: 'change_set', args: { tool_use_ids: items.map(i => i.tool_use_id) },
  safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300_000,
  change_set: state === 'malformed' ? { ...change_set, items: items.slice(0, 1) } : change_set } : null } as any)
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{display:'flex',height:'100vh'}}><ChatPanel/></div></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  // [name, state, ticks to clear (row numbers), modes, themes]
  const shots = [
    ['site-all-ticked', 'card', [], ['guided', 'expert'], ['dark', 'light']],
    ['site-one-unticked', 'card', [2], ['guided', 'expert'], ['dark', 'light']],
    ['site-none-ticked', 'card', [0, 1, 2, 3], ['guided', 'expert'], ['dark']],
    ['site-malformed', 'malformed', [], ['guided', 'expert'], ['dark']],
    ['profiles', 'profiles', [], ['guided', 'expert'], ['dark']],
    ['receipt-partial', 'partial', [], ['guided', 'expert'], ['dark', 'light']],
    ['receipt-every-status', 'all', [], ['guided', 'expert'], ['dark']],
    ['receipt-not-run', 'notrun', [], ['guided', 'expert'], ['dark']],
    ['settled-cancelled', 'cancelled', [], ['guided', 'expert'], ['dark']],
    ['settled-timeout', 'timeout', [], ['guided', 'expert'], ['dark']],
    ['settled-stopped', 'stopped', [], ['guided', 'expert'], ['dark']],
  ]
  for (const [name, state, clear, modes, themes] of shots) {
    for (const mode of modes) for (const theme of themes) {
      const page = await browser.newPage({ viewport: { width: 420, height: 1100 }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
      await page.route(url => url.pathname.startsWith('/api/'), route => {
        const p = new URL(route.request().url()).pathname
        if (p.endsWith('/chat/history')) return route.fulfill({ json: { turns: [], last_session_id: 'shot-session',
          bound_project: 'Demo', history_gap: 0, pending_turn: null } })
        return route.fulfill({ json: {} })
      })
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}&state=${state}&theme=${theme}`)
      await page.getByText('Fix the gaps on the Site card.').waitFor()
      for (const n of clear) await page.getByTestId('chat-change-set-tick').nth(n).click()
      // Nothing ticked: show the disabled Apply and its reason at the card's foot.
      if (clear.length && await page.getByTestId('chat-change-set-none').count())
        await page.getByTestId('chat-confirm-deny').scrollIntoViewIfNeeded()
      await page.waitForTimeout(400)
      const file = path.join(out, `lc47-${name}-${mode}-${theme}.png`)
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
