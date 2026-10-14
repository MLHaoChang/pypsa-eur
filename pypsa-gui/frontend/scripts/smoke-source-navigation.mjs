// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Native CP-03 browser smoke; intercepted HTTP, zero inference/media calls. */
import assert from 'node:assert/strict'
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
const entry = `.source-browser-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`), source = path.join(root, 'src', `${entry}.tsx`)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'source-browser-'))
const binding = { version: 1, project_id: 'project-1', project_name: 'Demo', scope: 'current', source: 'lopf', window_from: 0, window_to: 3, input_sha256: 'a'.repeat(64), data_sha256: 'b'.repeat(64) }
const view = { status: 'ok', binding, window: { from: 0, to: 3, snapshots: 4, weighted_hours: 4 },
  rows: [{ name: 'L_p1', class: 'Line', basis: 'active_power_proxy', util_pct: 60, rating_pct: 60, mean_util_pct: 50, peak_flow: 60, mean_flow: 50, peak_at: '2030-01-01 01:00', h_at_limit: 0, h_over_limit: 0, rating: { value: 100, basis: 'configured' }, s_max_pu: { min: 1, max: 1 } }],
  skipped: [{ name: 'L_zero', class: 'Line', reason: 'rating_missing_or_zero' }], series_index: ['00:00','01:00','02:00','03:00'],
  series: [{ name: 'L_p1', class: 'Line', util_pct: [60,60,40,40], flow: [60,60,40,40] }],
  claims: { full_ac_thermal_compliance: false, reason: 'Not proof of AC thermal compliance: no N-1 or voltage limits.' } }
let server, browser
const results = []
try {
  await fs.writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'; import {createRoot} from 'react-dom/client'
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'
import Results from './pages/Results'; import {useUIStore} from './store/uiStore'
import {startKeyResultsWalk} from './components/liveWalk'; import './index.css'
localStorage.setItem('results:active-tab','loadflow')
useUIStore.setState({currentProject:'Demo',uiMode:'expert',resultSource:'lopf',discussedRun:null,activeSlidePanel:'results'})
window.__ui=useUIStore; window.__steps=[]
window.__walk=()=>startKeyResultsWalk({onStep:tab=>window.__steps.push(tab.id)})
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><main style={{height:'100vh',width:'100vw'}} data-assurance-panel="results" data-assurance-project="Demo"><Results/></main></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(),tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined, headless: true, args: ['--no-sandbox'] })
  for (const scenario of [{name:'desktop',viewport:{width:1200,height:900}}, {name:'touch',viewport:{width:360,height:900},hasTouch:true,isMobile:true}, {name:'reduced-motion',viewport:{width:600,height:900},reducedMotion:'reduce'}]) {
    const {name,...options}=scenario, context=await browser.newContext(options), page=await context.newPage()
    const errors=[],requests=[]; let release,held=false,loadflowReads=0
    page.on('pageerror',e=>errors.push(e.message))
    await context.route(url=>url.protocol.startsWith('http')&&url.hostname!=='127.0.0.1',route=>route.abort())
    await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
      const url=new URL(route.request().url()),p=url.pathname; requests.push({path:p,method:route.request().method()}); let data=null
      if(p==='/api/results/loadflow-view') { loadflowReads++; if(!held){held=true;await new Promise(resolve=>{release=resolve})} data=view }
      else if(p==='/api/results/run-view') {
        if(url.searchParams.get('section')) {await route.fulfill({status:409,json:{detail:{error_kind:'evidence_unavailable',reason:'section_not_recorded'}}});return}
        data={status:'ok',binding:{version:1,project_id:'project-1',project_name:'Demo',scope:'historical',run_id:'old',section:null,baseline_run_id:'base',data_sha256:'c'.repeat(64)},data:{run_id:'old',status:'completed',metrics:{objective:{value:123,unit:'EUR'}},sections:[]}}
      } else if(p==='/api/network/snapshots') data={count:4,snapshots:['2030-01-01T00:00:00','2030-01-01T01:00:00','2030-01-01T02:00:00','2030-01-01T03:00:00'],weightings:[1,1,1,1],ts_start:null,ts_end:null}
      else if(p==='/api/simulation/status') data={running:false,status:'completed',objective:null,condition:'optimal'}
      else if(p==='/api/results/ac_pf/status') data={available:false}
      else if(p.startsWith('/api/network/')) data=[]
      await route.fulfill({status:200,json:data})
    })
    try {
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`)
      await page.getByRole('status').filter({hasText:'Loading matching load-flow'}).waitFor()
      await page.evaluate(()=>window.__walk())
      assert.deepEqual(await page.evaluate(()=>window.__steps),[])
      assert.equal(await page.locator('[data-result-binding]').count(),0)
      release(); await page.waitForFunction(()=>window.__steps.length===1)
      assert.deepEqual(await page.evaluate(()=>window.__steps),['loadflow'])
      await page.getByRole('cell',{name:'Line: L_p1',exact:true}).waitFor()
      assert((await page.getByRole('cell',{name:'Line: L_p1',exact:true}).locator('..').innerText()).includes('60.00'))
      await page.getByText('Line: L_zero — unavailable: rating_missing_or_zero',{exact:true}).waitFor()
      await page.getByTestId('results-group-investment').click()
      await page.getByTestId('results-tab-prices').click()
      assert.equal(await page.getByTestId('results-active-tab').getAttribute('data-active-results-tab'),'prices')
      const before=loadflowReads
      await page.evaluate(()=>window.__ui.getState().requestRunResult({runId:'old',baselineRunId:'base',resultsTab:'loadflow'}))
      await page.getByRole('status').filter({hasText:'Retained loadflow evidence unavailable'}).waitFor()
      assert.equal(loadflowReads,before,'Historical view substituted current data')
      assert.equal(await page.getByTestId('branch-loading').count(),0)
      await page.getByTestId('results-group-summary').click(); await page.getByText('123 EUR',{exact:true}).waitFor()
      assert.equal(await page.evaluate(()=>window.__ui.getState().discussedRun.runId),'old')
      assert.equal(await page.evaluate(()=>window.__ui.getState().discussedRun.baselineRunId),'base')
      assert(!requests.some(r=>r.method!=='GET'),'Read/navigation created work')
      assert.deepEqual(errors,[])
      await page.screenshot({path:path.join(artifacts,`${name}.png`),fullPage:true})
      results.push({scenario:name,passed:true,loadflowReads,inference_calls:0,work_writes:0})
    } catch(e){await fs.writeFile(path.join(artifacts,`${name}-failed.html`),await page.content());console.error(JSON.stringify({errors,artifacts,requests}));throw e}
    finally {await context.close()}
  }
  console.log(JSON.stringify({passed:true,browser:'native Chromium',api:'mocked HTTP',results,artifacts}))
} finally {await browser?.close();await server?.close();await fs.rm(html,{force:true});await fs.rm(source,{force:true})}
