// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** IB-C PR2 shots: AssumedChip in its three states, with and without a source, and AssumedKey,
 * light and dark (component harness, no API).
 * node scripts/assumed-chip-shots.mjs [out-dir]   (default: .scratch/ib-c/shots)
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entry = `.assumed-chip-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'ib-c', 'shots'))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body class="bg-bg text-text"><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AssumedChip, AssumedKey} from './components/AssumedChip'
import './index.css'
if (new URLSearchParams(location.search).get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
const SRC = { source: 'BMF AfA-Tabelle AV, row 3.1.3', sourceYear: 2000, note: 'battery class assumed' }
const rows: [string, any][] = [
  ['Assumed, with source', { ...SRC }],
  ['Assumed, dated source', { source: 'Fraunhofer ISE Stromgestehungskosten', sourceYear: '2024-07-01' }],
  ['Assumed, no source', {}],
  ['Confirmed, with source', { ...SRC, confirmed: true }],
  ['Confirmed, no source', { confirmed: true }],
  ['Stated (typed)', { ...SRC, typed: true }],
]
createRoot(document.getElementById('root')!).render(
  <div className="bg-panel text-text text-[11px]" style={{padding:16,maxWidth:640}} data-testid="harness">
    <table><tbody>{rows.map(([k, p]) => <tr key={k}><td className="pr-3 py-1 text-muted">{k}</td><td><AssumedChip {...p}/></td></tr>)}</tbody></table>
    <div className="mt-2"><AssumedKey/></div>
  </div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 680, height: 260 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}`)
    await page.getByTestId('harness').waitFor()
    const f = path.join(out, `ibc-pr2-assumed-chip-${theme}.png`)
    await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
