const fs=require('fs'),os=require('os'),path=require('path')
const {chromium}=require('/opt/node-tools/node_modules/playwright')
const BASE='http://127.0.0.1:5173', OUT=__dirname, PROJECT='Intake shots'
const pw=fs.readFileSync(path.join(os.homedir(),'.local/share/PyPSA Studio/dev-admin-password'),'utf8').trim()
const intake=JSON.parse(fs.readFileSync('/home/user/pypsa-eur/pypsa-gui/frontend/src/components/runMonitor/intakeCard.fixture.json','utf8'))
const D='a'.repeat(64)
const r=(i,param,label,value,unit,status,source,doc=false,mapped=false)=>({index:i,param,label,value,unit,status,source,from_document:doc,mapped})
const rows=[
 r(0,'connection_mw','Grid connection',12,'MW','proposed',"grid.pdf, page 1: 'Grid connection: 12 MW'",true),
 r(1,'critical_share','Critical load share',0.4,'fraction','proposed',"you wrote 'critical load is 40 %'"),
 r(2,'site_type','Site type','hospital',null,'proposed',"mapped by the assistant from: 'It is a hospital'",false,true),
 r(3,'genset_count','Existing gensets',3,null,'confirmed',"you wrote 'we have three gensets'"),
 r(4,'pv_mwp','Existing PV',2.5,'MW','struck',"you wrote 'maybe 2.5 MW of PV'"),
 r(5,'battery_mwh','Battery size',8,'MWh','conflict',"you wrote 'battery is 8 MWh'"),
 r(6,'battery_mwh','Battery size',10,'MWh','conflict',"battery.pdf, page 3: 'Capacity 10 MWh'",true)]
const SETUP={tool:'create_project_from_template',template:'eh_site',name:'Acme site',params_digest:D}
const REVIEW={tool_use_id:'tu_9',intent:'plan_review',title:'Own site study',question:'Approve this plan?',detail:'1. Brief\n2. Set up',options:[{label:'Approve'},{label:'Change'}],allow_free_text:true}
const cases={
 intake:{setup:true,intake:{card:{rows,defaults:intake.card.defaults},digest:D,missing:[]}},
 missing:{setup:true,intake:{card:{rows:rows.slice(0,3),defaults:intake.card.defaults},digest:D,missing:['peak_mw','site_type'],request:'peak load and the grid connection limit'}},
 long:{setup:true,intake:{card:{rows:rows.concat([
 r(7,'peak_mw','Peak load',6,'MW','proposed',"site.xlsx, sheet Load: 'Peak 6 MW'",true),
 r(8,'ride_through','Ride-through required','Yes',null,'confirmed',"you wrote 'must ride through'"),
 r(9,'fault_mva','Grid fault level',240,'MVA','struck',"grid.pdf, page 4: '240 MVA'",true),
 r(10,'horizon','Study horizon end',2045,null,'conflict',"you wrote 'until 2045'"),
 r(11,'horizon','Study horizon end',2040,null,'conflict',"plan.pdf, page 2: 'Horizon 2040'",true)]),defaults:intake.card.defaults},digest:D,missing:[]}},
 'long-buttons':null,
 plain:{setup:false}}
cases['long-buttons']={...cases.long,intake:{...cases.long.intake,missing:['peak_mw','site_type'],request:'peak load and the grid connection limit'}}
;(async()=>{
 const b=await chromium.launch(), probs=[]
 const o=await b.newContext(), l=await o.newPage()
 await l.goto(BASE); await l.locator('#email').fill('admin@example.com'); await l.locator('#password').fill(pw)
 await l.getByRole('button',{name:'Sign in',exact:true}).click(); await l.waitForURL(/\/projects/)
 await l.evaluate(async({n})=>{const{projectsApi}=await import('/src/api/projects.ts');const li=await projectsApi.list().catch(()=>[]);if(!li.some(p=>p.name===n))await projectsApi.save(n,false,true,undefined,true)},{n:PROJECT})
 const fx=await l.evaluate(async()=>{const m=await import('/src/components/runMonitor/runFixtures.ts');return JSON.parse(JSON.stringify({S:m.STATES,T:m.TASK_ID,E:m.ENVELOPE}))})
 const auth=await o.storageState(); await o.close()
 for(const mode of ['guided','expert']){
  const c=await b.newContext({storageState:auth,viewport:{width:1440,height:1300},reducedMotion:'reduce',deviceScaleFactor:1})
  await c.addInitScript(({mode,p})=>{localStorage.setItem('network-diagram:current-project',p);localStorage.setItem('network-diagram:ui-mode',mode);localStorage.setItem('network-diagram:ui-mode-explicit','1');localStorage.setItem('network-diagram:assistant-dock-width','520');localStorage.setItem('network-diagram:assistant-dock-pinned','pinned')},{mode,p:PROJECT})
  const page=await c.newPage(); page.setDefaultTimeout(10000)
  page.on('pageerror',e=>probs.push(mode+' pageerror '+e.message))
  let cur='plain'
  await page.route(u=>u.pathname.startsWith('/api/chat/runs/'),route=>{
    const cs=cases[cur]; const v=JSON.parse(JSON.stringify(fx.S.proposal))
    v.envelope={...fx.E,...(cs.setup?{setup:SETUP}:{})}; if(cs.intake)v.run.intake=cs.intake
    route.fulfill({json:v})})
  await page.route(u=>u.pathname==='/api/chat/activity',route=>route.fulfill({json:{version:1,project_id:PROJECT,filter:'all',cursor:'c:1',revision:1,resnapshot_required:false,next_page:null,total:0,warnings:[],scheduling_available:false,items:[]}}))
  await page.goto(BASE+'/'); await page.waitForLoadState('networkidle')
  const op=page.getByRole('link',{name:/Resume project/}); if(await op.count()){await op.first().click();await page.waitForURL(u=>!u.pathname.startsWith('/projects'));await page.waitForLoadState('networkidle');await page.waitForTimeout(1500)}
  for(const k of Object.keys(cases)){
   try{
    cur=k
    await page.evaluate(async({T,E,REVIEW,SETUP,withSetup})=>{
     const[{useChatStore},{useUIStore}]=await Promise.all([import('/src/store/chatStore.ts'),import('/src/store/uiStore.ts')])
     useUIStore.getState().setAssistantDockOpen(true)
     useChatStore.setState({pending:null,choice:null,messages:[]}); await new Promise(r=>setTimeout(r,50))
     useChatStore.setState({messages:[{id:'s'+Date.now(),ts:Date.now(),role:'tool',content:'',tool_use_id:'s-'+T,tool_name:'start_task',tool_result:{task_id:T,title:'Own site study',status:'ready',run:{stage:'intake'},envelope:{...E,...(withSetup?{setup:SETUP}:{})}}}],choice:REVIEW})
    },{T:fx.T,E:fx.E,REVIEW,SETUP,withSetup:cases[k].setup})
    const s=page.locator('[data-testid="envelope-section"]').first(); await s.waitFor()
    if(cases[k].setup) await page.locator('[data-testid="envelope-intake"]').waitFor()
    await page.waitForTimeout(400)
    await s.evaluate(e=>(e.closest('[data-testid^="chat-choice"]')??e).scrollIntoView({block:'start'}))
    const f=path.join(OUT,`${mode}-${k}.png`); await page.locator('[data-testid="assistant-dock"]').screenshot({path:f}); console.log(f)
    await s.evaluate(e=>{for(let n=e;n;n=n.parentElement)if(n.scrollHeight>n.clientHeight+5)n.scrollTop=n.scrollHeight});await page.waitForTimeout(300)
    const f2=path.join(OUT,`${mode}-${k}-bottom.png`);await page.locator('[data-testid="assistant-dock"]').screenshot({path:f2});console.log(f2)
   }catch(e){probs.push(mode+'-'+k+': '+e.message.split('\n')[0])}
  }
  await c.close()
 }
 console.log('PROBLEMS',probs); await b.close()
})()
