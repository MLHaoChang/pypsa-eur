// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for expert-catalogue 05 part B (spec §7.3): one
 * assistant reply with a backticked name shown as its plain name, an ambiguous
 * name left raw, a fenced code block untouched, a quote left as written, and the
 * name the user asked about left raw. Component-level harness (the chat panel
 * with a seeded transcript, as scripts/change-set-shots.mjs), API calls mocked,
 * no model calls. Dark and light.
 * NODE_PATH=/opt/node-tools/node_modules node scripts/chat-plain-names-shots.mjs [out-dir]
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
const entry = `.chat-plain-names-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'chat-plain-names-shots-'))
const reply = [
  '`e_nom` is the store’s size in MWh. Next to it:',
  '',
  '- `s_nom` and `p_set` read as the panel names them;',
  '- `p_nom` stays as written: a generator and a storage unit name it differently.',
  '',
  '> You wrote `e_nom` above.',
  '',
  'In a script:',
  '',
  '```python',
  'n.stores.loc["H2 tank", "e_nom"] = 200  # `e_nom`',
  '```',
].join('\n')
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
import './index.css'
const q = new URLSearchParams(location.search)
document.documentElement.dataset.theme = q.get('theme') === 'light' ? 'light' : 'dark'
const t0 = new Date(2026, 10, 28, 14, 2).getTime()
const messages: any[] = [
  { id: 'u', role: 'user', content: 'What does e_nom mean?', ts: t0 },
  { id: 'a', role: 'assistant', content: ${JSON.stringify(reply)}, ts: t0 },
]
useUIStore.setState({ currentProject: 'Demo', projectMismatch: null, assistantDockOpen: true, uiMode: 'expert' } as any)
useChatStore.setState({ sessionId: 'shot-session', messages, streaming: false, error: null, pending: null } as any)
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
  <div style={{display:'flex',height:'100vh'}}><ChatPanel/></div></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const theme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: 420, height: 760 }, deviceScaleFactor: 2 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      const p = new URL(route.request().url()).pathname
      if (p.endsWith('/chat/history')) return route.fulfill({ json: { turns: [], last_session_id: 'shot-session',
        bound_project: 'Demo', history_gap: 0, pending_turn: null } })
      return route.fulfill({ json: {} })
    })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}`)
    await page.getByText('What does e_nom mean?').waitFor()
    const shown = await page.locator('[data-role="assistant"]').innerText()
    for (const want of ['Rating and Demand read', 'e_nom is the store']) {
      if (!shown.includes(want)) throw new Error(`reply lacks "${want}": ${shown}`)
    }
    await page.waitForTimeout(400)
    const file = path.join(out, `expert-catalogue-05b-reply-${theme}.png`)
    await page.screenshot({ path: file, fullPage: true })
    console.log(file)
    await page.close()
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
