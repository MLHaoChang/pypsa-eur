import { chromium } from '/opt/codex/runtimes/cua/lib/node_modules/playwright-core/index.mjs'
import { readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import assert from 'node:assert/strict'
const dir='/workspace/scratch/pr712-ux-20261026', base='http://127.0.0.1:15172', name='Footprint UX review'
const evidence={sourceHead:execFileSync('git',['rev-parse','HEAD'],{cwd:'/workspace/asset-footprints',encoding:'utf8'}).trim(),application:'Actual React app + native authenticated FastAPI backend; isolated illustrative data-centre project',networkMocks:false,storeInjection:false,cases:[],screenshots:[],pageErrors:[],limitations:['Illustrative template and fixture coordinates; not a customer/surveyed campus.','External imagery/context availability is independent of footprint UX; no paid model/media calls.','Drawer/lock edge cases use existing public app actions after ordinary authentication, never store injection.']}
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader','--disable-dev-shm-usage']})
const ctx=await browser.newContext({viewport:{width:1920,height:1200},storageState:dir+'/app-session.json'})
const csrf=(await ctx.cookies()).find(c=>c.name==='pypsa_gui_csrf')?.value
const headers={'X-CSRF-Token':csrf}
const projects=await (await ctx.request.get(base+'/api/projects/')).json()
const project=projects.find(p=>p.name===name);assert(project?.id)
const lockUrl=base+'/api/projects/'+project.id+'/lock'
let second
const page=await ctx.newPage();page.setDefaultTimeout(15000)
page.on('pageerror',e=>evidence.pageErrors.push(e.message))
const shot=async file=>{await page.waitForTimeout(400);await page.screenshot({path:dir+'/'+file});evidence.screenshots.push(file);console.log('SHOT',file)}
const countEditor=async()=>assert.equal(await page.getByText('Footprint (optional)',{exact:true}).count(),1)
const summary=()=>page.getByText('Footprint (optional)',{exact:true})
const details=()=>page.locator('details').filter({has:summary()})
async function openEditor(){await countEditor();await summary().scrollIntoViewIfNeeded();if(!await details().evaluate(e=>e.open))await summary().click();await page.getByLabel('Width (m)',{exact:true}).waitFor()}
async function mode(m){await page.getByTestId('ui-mode-'+m).click();await page.waitForTimeout(500);const close=page.getByTitle('Close (Esc)',{exact:true});if(await close.count())await close.first().click();assert.equal(await page.getByTestId('ui-mode-'+m).getAttribute('aria-pressed'),'true')}
async function saveEnvelope(){await openEditor();await page.getByLabel('Width (m)',{exact:true}).fill('12.5');await page.getByLabel('Depth (m)',{exact:true}).fill('5.8');await page.getByLabel('Height (m, optional)',{exact:true}).fill('3');await page.getByRole('button',{name:'Save footprint',exact:true}).click();await page.getByText('Saved to project',{exact:true}).waitFor();assert.equal(await page.getByText(/Height omitted: library height estimate/).count(),0);assert.equal(await page.getByText('Envelope area: 72.5 m²',{exact:true}).count(),1);assert.equal(await page.getByText('Clearances and safety compliance require separate checks.',{exact:true}).count(),1);const doc=await (await ctx.request.get(base+'/api/projects/'+encodeURIComponent(name)+'/sites')).json();assert.deepEqual(doc.asset_footprints['StorageUnit:ups_battery'],{width_m:12.5,depth_m:5.8,height_m:3});evidence.cases.push('native save/read-back + height 3/no omitted hint + 72.5 area')}
try{
 // Persist fixture coordinates using the native model API before the app reloads it.
 const buses=await (await ctx.request.get(base+'/api/network/buses')).json();const bus=buses.find(b=>b.name==='it_bus')
 const update=await ctx.request.put(base+'/api/network/buses/it_bus',{headers,data:{...bus,x:5.1,y:52.1}});assert(update.ok())
 const saved=await ctx.request.post(base+'/api/projects/'+encodeURIComponent(name),{headers});assert(saved.ok())
 await page.goto(base+'/app?project='+project.id,{waitUntil:'domcontentloaded'})
 await page.getByTestId('ui-mode-expert').waitFor();await mode('expert')
 await page.getByRole('button',{name:/^Storage\s*\(2\)/}).click()
 await page.getByRole('row').filter({hasText:'ups_battery'}).click()
 for(const m of ['guided','expert']){
  await mode(m)
  await page.getByRole('button',{name:'Blank',exact:true}).click()
  await openEditor()
  const reset=page.getByRole('button',{name:'Use library estimate',exact:true})
  if(await reset.isEnabled()){await reset.click();await page.getByText('Library estimate: detailed footprint not provided; actual dimensions may differ.',{exact:true}).waitFor()}
  await summary().click();await shot(m+'-properties-collapsed.png')
  await openEditor();assert.equal(await page.getByText('Height omitted: library height estimate.',{exact:true}).count(),1)
  await shot(m+'-properties-open.png')
  await saveEnvelope();await shot(m+'-properties-saved.png')
  await page.getByRole('button',{name:'Site 3D',exact:true}).click()
  const sites=await (await ctx.request.get(base+'/api/projects/'+encodeURIComponent(name)+'/sites')).json()
  if(!sites.sites.length)await page.getByRole('button',{name:'Generate 3D site',exact:true}).click()
  await page.locator('[data-site-pane]').waitFor({state:'visible'});await openEditor()
  await page.getByText('Library dimensions are estimates unless a user-entered envelope is provided. Fit checks do not certify clearances or compliance.',{exact:true}).waitFor()
  await page.waitForFunction(()=>window.__site3d?.objects?.some(o=>o.key==='StorageUnit:ups_battery'))
  await page.waitForTimeout(1000)
  await countEditor();await shot(m+'-site.png')
  evidence.cases.push(m+': Properties collapsed/open; Site selected battery, exactly one editor, native 3D mesh and estimate note')
  if(m==='guided'){
   await page.keyboard.press('Control+j')
   await page.evaluate(async()=>{const {openFlowTargetInPlace}=await import('/src/layout/flowNavigation.ts');openFlowTargetInPlace({section:'assets',panel:'assets',component:'StorageUnit',entity:'ups_battery',field:'p_nom'})})
   await page.getByTestId('field-drawer').waitFor();await countEditor()
   assert.equal(await page.getByTestId('field-drawer').getByText('Footprint (optional)',{exact:true}).count(),1)
   await openEditor();await shot('guided-site-drawer.png')
   evidence.cases.push('Guided component drawer + expanded assistant dock: exactly one foreground footprint editor')
   await page.getByTestId('field-drawer-close').click();await page.keyboard.press('Control+j');await countEditor()
  }else{
   await page.getByRole('button',{name:'Hide properties panel',exact:true}).click();await openEditor();await countEditor()
   assert.equal(await page.locator('[data-site-pane]').getByText('Footprint (optional)',{exact:true}).count(),1)
   await shot('expert-site-overlay.png')
   await page.getByRole('button',{name:'Show properties panel',exact:true}).click();await openEditor();await countEditor()
   evidence.cases.push('Expert Properties hide/show: Site overlay fallback moves ownership without duplication')
  }
 }
 // A second real test identity takes the native lock. No UI state is injected.
 second=await browser.newContext()
 const login=await second.request.post(base+'/api/auth/login',{data:{email:'fixture-lock@gridspine.localhost',password:(await readFile(dir+'/second-password','utf8')).trim()}});assert(login.ok())
 const secondHeaders={'X-CSRF-Token':(await login.json()).csrf_token}
 for(const m of ['expert','guided']){
  await mode(m);await openEditor();await saveEnvelope()
  const released=await ctx.request.delete(lockUrl,{headers});assert(released.ok())
  const acquired=await second.request.post(lockUrl,{headers:secondHeaders});assert(acquired.ok())
  await page.evaluate(async project=>{const {acquireProjectLock}=await import('/src/utils/projectActions.ts');await acquireProjectLock(project)},name)
  const save=page.getByRole('button',{name:'Save footprint',exact:true}),reset=page.getByRole('button',{name:'Use library estimate',exact:true})
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='Save footprint'&&b.matches(':disabled')))
  assert.equal(await page.getByText('Saved to project',{exact:true}).count(),0)
  assert(await save.isDisabled());assert(await reset.isDisabled())
  const opacities=await Promise.all([save.evaluate(e=>getComputedStyle(e).opacity),reset.evaluate(e=>getComputedStyle(e).opacity)])
  assert.deepEqual(opacities,['0.5','0.5'])
  evidence.cases.push({case:m+': native lock transition clears saved status and visibly disables both actions',computedOpacities:opacities})
  await shot(m+'-readonly.png')
  const unheld=await second.request.delete(lockUrl,{headers:secondHeaders});assert(unheld.ok())
  await page.evaluate(async project=>{const {acquireProjectLock}=await import('/src/utils/projectActions.ts');await acquireProjectLock(project)},name)
 }
 assert.deepEqual(evidence.pageErrors,[])
 evidence.result='passed'
}catch(e){evidence.result='failed';evidence.failure=e.stack;await page.screenshot({path:dir+'/capture-failure.png'}).catch(()=>{});throw e}
finally{await writeFile(dir+'/real-app-browser.json',JSON.stringify(evidence,null,2));await second?.close();await browser.close()}
