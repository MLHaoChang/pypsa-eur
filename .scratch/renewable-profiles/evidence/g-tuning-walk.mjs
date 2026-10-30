// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import './g-bootstrap.mjs'
const { chromium } = await import(process.env.GRIDSPINE_PLAYWRIGHT ?? 'playwright-core')
import { readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import assert from 'node:assert/strict'
const dir=process.env.GRIDSPINE_EVIDENCE_DIR ?? '/tmp/gridspine-gb-evidence', base=process.env.GRIDSPINE_BASE_URL ?? 'http://127.0.0.1:15173'
const fixture=JSON.parse(await readFile(dir+'/browser-fixture.json','utf8'))
const browser=await chromium.launch({executablePath:process.env.GRIDSPINE_CHROMIUM ?? '/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const context=await browser.newContext({viewport:{width:1440,height:900},storageState:dir+'/browser-session.json'})
const headers={'X-CSRF-Token':(await context.cookies()).find(c=>c.name==='pypsa_gui_csrf')?.value}
const page=await context.newPage();page.setDefaultTimeout(30000)
const evidence={sourceHead:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),viewport:[1440,900],networkMocks:false,storeInjection:false,
  authentication:'Native authenticated fixture account; ordinary owned project and lock',navigation:'Existing public openTimeSeriesFor action, plus ordinary visible UI controls',weather:'synthetic only',shots:[],pageErrors:[],cases:[],faults:['Chromium blocks the comparison URL for the refusal/Retry transport-fault screenshot; no HTTP interception or mocked payloads.']}
page.on('pageerror',e=>evidence.pageErrors.push(e.message))
const cdp=await context.newCDPSession(page)
async function latency(ms){await cdp.send('Network.enable');await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:ms,downloadThroughput:-1,uploadThroughput:-1})}
async function shot(file){await page.screenshot({path:dir+'/'+file});evidence.shots.push(file);console.log('SHOT',file)}
async function api(path,data,method='post'){
  const res=await context.request[method](base+'/api/'+path,{headers,...(data===undefined?{}:{data})});assert(res.ok(),path+': '+res.status());return res.json()
}
async function open(name){
  await page.goto(base+'/app?project='+fixture.project,{waitUntil:'domcontentloaded',timeout:120000})
  await page.getByTestId('ui-mode-expert').waitFor();await page.locator('[role=tab][aria-selected=true]').filter({hasText:fixture.name}).waitFor();await page.getByTestId('ui-mode-expert').click()
  await page.evaluate(async name=>(await import('/src/layout/flowNavigation.ts')).openTimeSeriesFor('Generator',name),name)
  await page.getByRole('button',{name:/^Renewables/}).click();await page.getByTestId('tune-'+name).waitFor()
}
async function tune(name){await page.getByTestId('tune-'+name).click();await page.getByLabel('Preset',{exact:true}).waitFor()}
async function generate(){await page.getByRole('dialog').getByRole('button',{name:'Generate',exact:true}).click()}
try{
  await open(fixture.generator);await page.getByTestId('weather-window').waitFor()
  await shot('g-expert-renewables.png');await shot('g-weather-window.png')
  await tune(fixture.generator)
  await shot('g-tune-untouched.png');await shot('g-era5-disabled.png')
  assert.equal(await page.getByText('Needs ERA5 weather: switch the weather source to use it').count(),4)
  const loss=page.getByLabel('Losses (fraction)',{exact:true});await loss.fill('0.1')
  await page.getByTestId('tune-changed-losses').waitFor();await shot('g-tune-changed.png')
  await page.getByText('Advanced',{exact:true}).click();await page.getByLabel('Advanced settings',{exact:true}).fill('[]');await generate()
  await page.getByText('Advanced settings must be a JSON object.',{exact:true}).waitFor();await shot('g-json-error.png')
  await page.getByLabel('Advanced settings',{exact:true}).fill('');await page.getByText('Advanced',{exact:true}).click()
  await latency(1800);await generate()
  await page.getByText('Loading comparison…',{exact:true}).waitFor()
  assert.equal(await page.getByRole('dialog').getByRole('button',{name:'Apply',exact:true}).isEnabled(),false)
  await shot('g-loading.png');await shot('g-disabled-apply.png')
  await page.getByTestId('compare-week').waitFor();await latency(0)
  await page.getByTestId('compare-week').scrollIntoViewIfNeeded();await shot('g-compare-week.png')
  await page.getByRole('row',{name:/Full-load hours/}).scrollIntoViewIfNeeded();await shot('g-compare-statistics.png')
  await loss.fill('0.2');await page.getByText('This preview is out of date: generate again',{exact:true}).scrollIntoViewIfNeeded()
  assert.equal(await page.getByRole('dialog').getByRole('button',{name:'Apply',exact:true}).isEnabled(),false)
  await shot('g-out-of-date.png')
  await loss.fill('0.1');await latency(1800)
  await cdp.send('Network.setBlockedURLs', {urls:['*/profiles/compare*']})
  await generate()
  await page.getByText(/Couldn't compare:/).waitFor();await page.getByRole('button',{name:'› Retry',exact:true}).scrollIntoViewIfNeeded()
  await shot('g-refusal.png')
  await cdp.send('Network.setBlockedURLs', {urls:[]});await latency(0)
  await page.getByRole('button',{name:'› Retry',exact:true}).click();await page.getByTestId('compare-week').waitFor()
  const before=await (await context.request.get(base+'/api/network/undo/info')).json()
  const appliedResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/profiles/apply'))
  await page.getByRole('dialog').getByRole('button',{name:'Apply',exact:true}).click()
  const applied=await (await appliedResponse).json();assert.equal(applied.component,fixture.generator);assert.equal(applied.hours,168)
  await page.getByTestId('generate-notice').waitFor()
  const after=await (await context.request.get(base+'/api/network/undo/info')).json();assert.equal(after.depth,before.depth+1)
  evidence.cases.push({case:'native apply receipt and exactly one undo step',component:applied.component,hours:applied.hours,undoBefore:before.depth,undoAfter:after.depth})
  await shot('g-apply-receipt.png')
  const gens=await (await context.request.get(base+'/api/network/generators')).json()
  const pv=gens.find(g=>g.name===fixture.generator), candidate='candidate_pv'
  if(!gens.some(g=>g.name===candidate))await api('network/generators',{name:candidate,bus:pv.bus,carrier:'solar',p_nom:10.,marginal_cost:0.})
  await api('projects/'+encodeURIComponent(fixture.name))
  await open(candidate);await tune(candidate)
  const newOnlyResponse=page.waitForResponse(r=>r.url().includes('/profiles/compare'))
  await generate();const response=await newOnlyResponse;assert(response.ok());assert(!new URL(response.url()).searchParams.has('a'))
  const data=await response.json();assert.equal(data.a,null);assert.equal(data.delta,null);assert.equal(data.week.a,null)
  await page.getByText('No current profile: showing the new profile only',{exact:true}).waitFor()
  assert.equal(await page.getByRole('columnheader',{name:'Current',exact:true}).count(),0)
  assert.equal(await page.getByRole('columnheader',{name:'Change',exact:true}).count(),0)
  await page.getByTestId('compare-week').scrollIntoViewIfNeeded();await shot('g-new-only.png')
  evidence.cases.push({case:'native New-only response',a:data.a,delta:data.delta,weekA:data.week.a,flh:data.b.flh})
  await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click()
  await page.getByTestId('ui-mode-guided').click()
  await page.evaluate(async name=>(await import('/src/layout/flowNavigation.ts')).openTimeSeriesFor('Generator',name),candidate)
  await page.getByTestId('weather-bar').waitFor();assert.equal(await page.getByTestId(/^tune-/).count(),0)
  await shot('g-guided-renewables.png')
  const pending=await (await context.request.get(base+'/api/projects/'+encodeURIComponent(fixture.name)+'/profiles/pending')).json()
  assert(pending.pending.every(p=>p.source==='synthetic'))
  evidence.cases.push({case:'all native pending artifacts use synthetic weather',count:pending.pending.length})
  assert.deepEqual(evidence.pageErrors,[])
  console.log('DONE',JSON.stringify(evidence))
}finally{
  await writeFile(dir+'/real-app-evidence.json',JSON.stringify(evidence,null,2))
  await browser.close()
}
