// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Dev-only UX-review shots for live-voice 16: a plan whose solve step's job
 * was stopped reads "stopped", not "failed", in the plan card, Guided and
 * Expert, dark and light. Component-level harness, no API or model calls.
 * node scripts/lv16-stopped-step-shots.mjs [out-dir]   (default: a new temp dir)
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
const entry = `.lv16-shots-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = process.argv[2] || await fs.mkdtemp(path.join(os.tmpdir(), 'lv16-shots-'))
let server, browser
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import AssistantResultCard from './components/AssistantResultCard'
import {useUIStore} from './store/uiStore'
import './index.css'
const q = new URLSearchParams(location.search)
const mode = q.get('mode') === 'expert' ? 'expert' : 'guided'
const status = q.get('status') ?? 'cancelled'
document.documentElement.dataset.theme = q.get('theme') === 'light' ? 'light' : 'dark'
useUIStore.setState({ uiMode: mode })
const words: Record<string, string> = { set_component_attribute: 'change a value', run_simulation: 'solve the model', create_chart: 'make a chart' }
const task = { task_id: '0ba037fe-bc46-43d3-a9c2-9b587c9cd50e', title: 'Raise the PV cap and re-solve', status: 'ready',
  completed_steps: 1, total_steps: 3,
  steps: [{ id: 'cap', tool: 'set_component_attribute', status: 'completed' },
    { id: 'run', tool: 'run_simulation', status }, { id: 'chart', tool: 'create_chart', status: 'pending' }],
  next_step: { id: 'run', tool: 'run_simulation', status } }
createRoot(document.getElementById('root')!).render(
  <div className="bg-bg text-fg p-3" style={{width: 380, minHeight: '100vh'}}>
    <AssistantResultCard result={{ _task: task }} disabled={false} onRequest={() => {}}
      toolWords={mode === 'guided' ? (t: string) => words[t] ?? t : undefined} />
  </div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const shots = [['stopped', 'cancelled'], ['before-failed', 'failed']]
  for (const [name, status] of shots) for (const mode of ['guided', 'expert']) for (const theme of ['dark', 'light']) {
    if (name === 'before-failed' && theme === 'light') continue
    const page = await browser.newPage({ viewport: { width: 404, height: 260 }, deviceScaleFactor: 2 })
    await page.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}&status=${status}&theme=${theme}`)
    await page.getByTestId('assistant-task-card').waitFor()
    await page.waitForTimeout(300)
    const file = path.join(out, `lv16-${name}-${mode}-${theme}.png`)
    await page.screenshot({ path: file, fullPage: true })
    console.log(file)
    await page.close()
  }
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
