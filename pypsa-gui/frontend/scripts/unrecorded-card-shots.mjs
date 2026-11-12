// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for RP-20 (#1396): the confirm_unrecorded_series
 * card, one series and 24 (in a dock-height box: top, then scrolled to
 * Approve), Guided and Expert. Component-level harness, no API calls.
 * node scripts/unrecorded-card-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.unrecorded-card-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'unrecorded-card-shots-'))
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
const n = Number(q.get('n') || 1)
const hex = (i: number) => (0x1a2b3c4d5e6f7a8bn + BigInt(i) * 0x9e3779b97f4a7c15n).toString(16).slice(-16).padStart(16, '0')
const rows: any[] = Array.from({ length: n }, (_, i) => ({
  component_class: 'Generator', name: n === 1 ? 'Wind Farm North' : 'wind_' + String(i + 1).padStart(2, '0'),
  field: 'provenance', label: 'Series record', old: 'no record \u00b7 values ' + hex(i), old_code: hex(i),
  new: "the network's own", unit: '', role: 'typed', digest: hex(i),
  target: { section: 'profiles', panel: 'timeseries', component: 'Generator', entity: 'wind', field: 'provenance' } }))
rows.push({ component_class: '', name: '', field: 'note_0', label: 'note_0', old: null, new: null, unit: '',
  role: 'note', note: 'The code after each series identifies the values you are confirming.' })
const edit_card = { version: 1, kind: 'series_edit', tool: 'confirm_unrecorded_series', complete: true,
  says: 'Records ' + n + " series as the network's own. Their values are unchanged", digest: 'd'.repeat(64),
  section: { id: 'profiles', label: 'Profiles' }, rows }
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: mode } as any)
useChatStore.setState({ sessionId: 'shot-session', pending: {
  tool_use_id: 'call-1', tool_name: 'confirm_unrecorded_series',
  args: { project_id: 'Demo', items: rows.filter(r => r.role === 'typed').map(r => ({ component: r.name, digest: r.digest })) },
  safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300,
  expires_at_epoch_ms: Date.now() + 300_000, edit_card } } as any)
createRoot(document.getElementById('root')!).render(
  <div id="dock" style={{width: 400, maxHeight: 640, overflowY: 'auto', padding: 8}}><ConfirmationCard/></div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const n of [1, 24]) {
    for (const mode of ['guided', 'expert']) {
      const page = await browser.newPage({ viewport: { width: 420, height: 660 }, deviceScaleFactor: 2 })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
      await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({ json: {} }))
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}&n=${n}`)
      await page.getByTestId('chat-confirm-approve').waitFor()
      await page.waitForTimeout(300)
      const shoot = async (suffix) => {
        const file = path.join(out, `rp20-card-${n}-${mode}${suffix}.png`)
        const box = await page.evaluate(() => document.getElementById('dock').getBoundingClientRect().bottom)
        await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 420, height: Math.ceil(box) + 8 } })
        console.log(file)
      }
      await shoot('')
      if (n > 1) {
        await page.getByTestId('chat-confirm-approve').scrollIntoViewIfNeeded()
        await page.waitForTimeout(150)
        await shoot('-scrolled')
      }
      await page.close()
    }
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
