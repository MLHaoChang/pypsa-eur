// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Real frontend HTTP wire, mocked backend; no inference, media or jobs. */
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
const entry = `.gap-browser-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`), source = path.join(root, 'src', `${entry}.tsx`)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'gap-followup-browser-'))
const ref = { source_id: 'a'.repeat(64), revision: 1, digest: 'b'.repeat(64) }
const scope = { version: 1, profile: 'generator-dispatch/1', project_id: 'owned-project',
  entity_id: '<img src=x onerror=alert(1)>', metrics: ['dispatch_energy'], retain: false }
const prepared = { message: 'Recheck the exact request shown in the checked follow-up card.',
  checked_scope: scope, source_gap_ref: ref }
let server, browser
const results = []
try {
  await fs.writeFile(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'
import AssistantActivity from './components/AssistantActivity'
import ChatPanel from './components/ChatPanel'
import {useUIStore} from './store/uiStore'
import {useChatStore} from './store/chatStore'
import './index.css'
useUIStore.setState({currentProject:'Demo',projectMismatch:null,assistantDockOpen:true,assistantSpeakEnabled:false})
useChatStore.setState({sessionId:'current-chat'})
window.__chat=useChatStore
function App(){const [chat,setChat]=useState(true);return <main style={{display:'flex',flexDirection:'column',height:'100vh',maxWidth:420,marginLeft:'auto',minWidth:0}}>
<nav><button onClick={()=>setChat(true)}>Conversation</button><button onClick={()=>setChat(false)}>Activity</button></nav>
<div style={{display:chat?'none':'flex',flex:1,minHeight:0}}><AssistantActivity onConversation={()=>setChat(true)}/></div>
<div style={{display:chat?'flex':'none',flex:1,minHeight:0,minWidth:0}}><ChatPanel/></div></main>}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] })
  for (const scenario of [
    { name: 'desktop', viewport: { width: 1100, height: 900 } },
    { name: 'touch', viewport: { width: 320, height: 900 }, hasTouch: true, isMobile: true },
    { name: 'reduced-motion', viewport: { width: 420, height: 900 }, reducedMotion: 'reduce' },
  ]) {
    const {name,...options}=scenario
    const context=await browser.newContext(options)
    await context.route(url=>url.protocol.startsWith('http')&&url.hostname!=='127.0.0.1',route=>route.abort())
    const page=await context.newPage(), posts=[], unexpected=[], errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
      const request=route.request(),url=new URL(request.url()),p=url.pathname
      if(request.method()==='POST'&&p==='/api/chat/stream'){
        posts.push(request.postDataJSON())
        return route.fulfill({contentType:'text/event-stream',body:'event: error\ndata: '+JSON.stringify({error_kind:'source_gap_followup_unavailable',message:'Refresh Activity and prepare the changed gap again.'})+'\n\nevent: session_done\ndata: {"reason":"source_gap_followup_unavailable"}\n\n'})
      }
      if(request.method()!=='GET'){unexpected.push(p);return route.abort()}
      let data={}
      if(p==='/api/chat/activity')data={version:1,project_id:'owned-project',filter:'all',cursor:'epoch:1',revision:1,resnapshot_required:false,next_page:null,total:1,warnings:[],scheduling_available:false,
        items:[{id:'source_gap:old',source_id:ref.source_id,project_id:'owned-project',family:'source_gap',title:'Checked answer source gap',status:'needs_review',native_status:'source_gap_recorded',needs_you:true,evidence_available:false,
          session_id:'lost-chat',decision_id:ref.source_id,decision_revision:ref.revision,decision_digest:ref.digest}]}
      else if(p==='/api/chat/decisions/'+ref.source_id){
        assert.equal(url.searchParams.get('revision'),'1');assert.equal(url.searchParams.get('digest'),ref.digest)
        data={source_id:ref.source_id,project_id:'owned-project',session_id:'lost-chat',tool_use_id:'old-turn',tool_name:'checked_answer',native_status:'source_gap_recorded',revision:1,decision_digest:ref.digest,inspection_only:true,evidence_available:false,question:null,
          reason:'A historical gap does not establish current evidence.',source_gap:{answer_id:'old-answer',turn_id:'old-turn',request_scope:scope,slots:[{metric:'dispatch_energy',disposition:'unavailable',reason:'unverified_solve'}]},prepared_followup:prepared}
      }else if(p==='/api/chat/reliability/capabilities')data={version:1,mode:'pilot',profiles:{'generator-dispatch/1':{enabled:true}}}
      else if(p==='/api/chat/profiles')data={profiles:[],active_profile_id:null}
      else if(p==='/api/chat/health')data={chat_ready:true}
      else if(p==='/api/chat/history')data={turns:[],last_session_id:'current-chat',bound_project:'Demo',history_gap:0,pending_turn:null}
      else if(p==='/api/chat/workflows')data={workflows:[],context:'expert'}
      else if(p==='/api/chat/external/pending')data={pending:[]}
      else if(p==='/api/chat/settings/api-key')data={configured:true,source:'env',hint:'fixture'}
      else if(p.endsWith('/uploads'))data=[]
      else if(p==='/api/dictation/config')data={available:false,model:'',languages:['en'],max_bytes:10485760,max_recording_seconds:120}
      else if(p.includes('/meta'))data={name:'Demo',bus_count:1,snapshot_count:2}
      else if(p.includes('/status'))data={running:false,status:'idle'}
      return route.fulfill({json:data})
    })
    try{
      await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html`)
      await page.getByTestId('chat-input').fill('Keep this unsent message.')
      await page.evaluate(()=>window.__chat.setState({attachedFileIds:['keep-file']}))
      await page.getByRole('button',{name:'Activity',exact:true}).click()
      await page.getByRole('button',{name:'Inspect source gap',exact:true}).click()
      await page.getByRole('button',{name:'Prepare checked follow-up',exact:true}).waitFor()
      await page.getByRole('button',{name:'Prepare checked follow-up',exact:true}).click()
      await page.getByRole('region',{name:'Prepared checked follow-up'}).waitFor()
      assert.equal(posts.length,0)
      assert.equal(await page.getByTestId('chat-input').inputValue(),'Keep this unsent message.')
      assert.equal(await page.getByRole('checkbox',{name:'Check answers',exact:true}).isChecked(),false)
      assert.equal(await page.getByRole('region',{name:'Prepared checked follow-up'}).locator('img').count(),0)
      await page.getByRole('button',{name:'Send checked follow-up',exact:true}).click()
      await page.getByText('Prepare this follow-up again',{exact:true}).waitFor()
      assert.equal(posts.length,1)
      assert.deepEqual(posts[0].assurance,{version:1,mode:'strict'})
      assert.deepEqual(posts[0].checked_scope,scope)
      assert.deepEqual(posts[0].source_gap_ref,ref)
      assert.equal(posts[0].session_id,'current-chat')
      assert.equal(posts[0].attachment_file_ids,undefined)
      assert.equal(await page.getByTestId('chat-input').inputValue(),'Keep this unsent message.')
      assert.deepEqual(await page.evaluate(()=>window.__chat.getState().attachedFileIds),['keep-file'])
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false)
      assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[])
      await page.screenshot({path:path.join(artifacts,`${name}.png`)})
      results.push({name,pass:true,explicit_checked_sends:posts.length,other_mutations:0})
    }finally{await context.close()}
  }
  console.log(JSON.stringify({artifacts,results},null,2))
}finally{
  await browser?.close();await server?.close()
  await fs.rm(html,{force:true});await fs.rm(source,{force:true})
}
