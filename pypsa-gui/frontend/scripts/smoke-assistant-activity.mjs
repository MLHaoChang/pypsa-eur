// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Activity browser contract. All HTTP is mocked; no inference, media or jobs. */
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
const entry = `.activity-browser-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'activity-browser-'))
let server, browser
const results = []
try {
  await fs.writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import AssistantActivity from './components/AssistantActivity'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import './index.css'
useUIStore.setState({currentProject:'Demo',projectMismatch:null})
useChatStore.setState({sessionId:'s1'})
window.__opened=0
createRoot(document.getElementById('root')!).render(<main style={{display:'flex',height:'100vh',maxWidth:420,marginLeft:'auto'}}><AssistantActivity onConversation={()=>window.__opened++}/></main>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] })
  for (const scenario of [
    { name: 'desktop', viewport: { width: 1100, height: 850 } },
    { name: 'touch', viewport: { width: 320, height: 850 }, hasTouch: true, isMobile: true },
    { name: 'reduced-motion', viewport: { width: 420, height: 850 }, reducedMotion: 'reduce' },
  ]) {
    const {name, ...options} = scenario
    const context = await browser.newContext(options)
    await context.route(url => url.protocol.startsWith('http') && url.hostname !== '127.0.0.1', route => route.abort())
    const page = await context.newPage()
    const errors = [], mutations = []
    let reads = 0, fail = false
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const request = route.request(), url = new URL(request.url())
      if (request.method() !== 'GET' || url.pathname !== '/api/chat/activity') {
        mutations.push(url.pathname); return route.abort()
      }
      reads++
      if (fail) return route.fulfill({status:503,json:{detail:'fixture disconnected'}})
      const filter = url.searchParams.get('filter')
      const items = [
        {id:'solve:1',source_id:'job-1',project_id:'p1',family:'solve_queue',title:'Network solve',status:'running',native_status:'running',needs_you:false,evidence_available:false},
        {id:'confirmation:1',source_id:'opaque-decision',project_id:'p1',family:'confirmation',title:'Update generator capacity',status:'waiting_for_approval',native_status:'waiting_for_approval',needs_you:true,evidence_available:false,session_id:'s1'},
        {id:'foreground_simulation:old',source_id:'old-job',project_id:'p1',family:'foreground_simulation',title:'Previous network solve',status:'needs_review',native_status:'interrupted',needs_you:true,evidence_available:false,observation:'retained',observed_at:1792000000,reason:'Last observed work has no matching live worker; it was not restarted.'},
      ].filter(row => filter === 'needs_you' ? row.needs_you : filter === 'scheduled' ? false : true)
      return route.fulfill({json:{version:1,project_id:'p1',filter,cursor:'epoch:1',revision:1,resnapshot_required:false,items,next_page:null,total:items.length,warnings:['activity_history_is_bounded_observation'],scheduling_available:false}})
    })
    try {
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`)
      await page.getByText('Network solve', {exact:true}).waitFor()
      await page.getByRole('button',{name:'Needs you',exact:true}).click()
      await page.getByRole('button',{name:'Open originating chat'}).waitFor()
      await page.getByText('Previous network solve', {exact:true}).waitFor()
      await page.getByText(/Retained observation/).waitFor()
      assert.equal(await page.getByRole('button', {name:/approve|retry|resume|cancel/i}).count(),0)
      assert.equal(await page.getByText('Network solve',{exact:true}).count(),0)
      await page.getByRole('button',{name:'Open originating chat'}).click()
      assert.equal(await page.evaluate(()=>window.__opened),1)
      assert.deepEqual(mutations,[])
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > window.innerWidth),false)
      await page.screenshot({path:path.join(artifacts,`${name}.png`)})
      fail = true
      await page.evaluate(()=>window.dispatchEvent(new Event('online')))
      await page.getByRole('alert').waitFor()
      fail = false
      await page.evaluate(()=>window.dispatchEvent(new Event('online')))
      await page.getByRole('alert').waitFor({state:'detached'})
      await page.getByRole('button',{name:'Scheduled',exact:true}).click()
      await page.getByText('Scheduled work is not available yet.').waitFor()
      assert.deepEqual(errors,[])
      assert.deepEqual(mutations,[])
      results.push({name,pass:true,reads,mutations:0})
    } finally { await context.close() }
  }
  console.log(JSON.stringify({artifacts,results},null,2))
} finally {
  await browser?.close()
  await server?.close()
  await fs.rm(html,{force:true});await fs.rm(source,{force:true})
}
