// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** te 04 slice 3 shots: the data-centre set-up fields in each state (component harness, stubbed API),
 * light and dark. node scripts/dc-fields-shots.mjs [out-dir]   (default: .scratch/template-e2e/shots)
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
const entry = `.dc-fields-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'template-e2e', 'shots'))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body class="bg-bg text-text"><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {DcLoadFields, DcBackupArchFields} from './pages/hubDesign/cards/DcSetupFields'
import ResultsOutOfDate from './components/ResultsOutOfDate'
import {campusApi} from './api/campusElectrical'
import {resultsApi} from './api/simulation'
import {useUIStore} from './store/uiStore'
import './index.css'
const q = new URLSearchParams(location.search)
if (q.get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
const state = q.get('state')
const SPEC = { pue_at_design: 1.3, critical_share: 0.75, architecture: 'ac', backup: { mode: 'genset' } }
;(resultsApi as any).getEhTemplate = async () => ({ dc_load: { spec: SPEC } })
const reject = (detail: unknown) => async () => { throw { response: { status: 422, data: { detail } } } }
;(campusApi as any).setDcLoad = state === 'edited'
  ? reject({ error_kind: 'dc_series_edited', message: 'edited' })
  : reject({ error_kind: 'dc_load_invalid', message: 'the shares exceed 1', field: 'dc_load.critical_share' })
useUIStore.setState({ currentProject: 'Site A', readOnly: state === 'viewer', readOnlyReason: state === 'viewer' ? 'locked-by-user' : 'writable' } as any)
const body = state === 'results'
  ? <ResultsOutOfDate />
  : state === 'expert-arch'
    ? <DcBackupArchFields only="architecture" />
    : <><DcLoadFields /><div className="mt-3"><DcBackupArchFields /></div></>
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <div className="bg-panel text-text text-[11px]" style={{padding:16,maxWidth:520}} data-testid="harness">{body}</div>
  </QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const states = {
    'guided': async () => {},
    'pue-invalid': async p => {
      await p.getByLabel('PUE at design').fill('0.9'); await p.getByLabel('Critical share (%)').fill('120')
      await p.getByTestId('dc-load-invalid').waitFor()
    },
    'refusal': async p => {
      await p.getByLabel('Critical share (%)').fill('90'); await p.getByRole('button', { name: 'Save' }).click()
      await p.getByTestId('dc-load-refusal').waitFor()
    },
    'edited': async p => {
      await p.getByLabel('PUE at design').fill('1.5'); await p.getByRole('button', { name: 'Save' }).click()
      await p.getByTestId('dc-series-edited').waitFor()
    },
    'viewer': async () => {},
    'expert-arch': async () => {},
    'results': async () => {},
  }
  const base = `http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`
  for (const theme of ['light', 'dark']) {
    for (const [name, act] of Object.entries(states)) {
      const page = await browser.newPage({ viewport: { width: 560, height: 360 }, deviceScaleFactor: 2 })
      page.on('pageerror', e => { pageErrors.push(e.message) })
      await page.goto(`${base}?theme=${theme}&state=${name}`)
      await page.getByTestId('harness').waitFor()
      if (name !== 'results') await page.getByLabel(name === 'expert-arch' ? 'Power architecture' : 'PUE at design').first().waitFor().catch(() => {})
      await act(page)
      const f = path.join(out, `04-dc-fields-${name}-${theme}.png`)
      await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
      await page.close()
    }
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
