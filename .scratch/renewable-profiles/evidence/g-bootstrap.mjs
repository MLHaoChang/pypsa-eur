// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
const { chromium } = await import(process.env.GRIDSPINE_PLAYWRIGHT ?? 'playwright-core')
import { mkdir, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
const base=process.env.GRIDSPINE_BASE_URL ?? 'http://127.0.0.1:15173', dir=process.env.GRIDSPINE_EVIDENCE_DIR ?? '/tmp/gridspine-gb-evidence'
await mkdir(dir,{recursive:true})
assert(process.env.GRIDSPINE_AUTH_STATE, 'Supply a private authenticated Playwright storage-state path')
const browser=await chromium.launch({executablePath:process.env.GRIDSPINE_CHROMIUM ?? '/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const context=await browser.newContext({viewport:{width:1440,height:900},storageState:process.env.GRIDSPINE_AUTH_STATE})
let response=await context.request.get(base+'/api/projects/')
assert(response.ok(),'Authenticate in the isolated review app before running this walk')
const csrf=(await context.cookies()).find(c=>c.name==='pypsa_gui_csrf')?.value
const headers={'X-CSRF-Token':csrf}
async function request(path, data, method='post') {
  const res=await context.request[method](base+'/api/'+path,{headers,...(data===undefined?{}:{data})})
  assert(res.ok(),path+': '+res.status()+' '+(await res.text()).slice(0,300))
  return res.json()
}
const name='G-b profile tuning review'
let projects=await (await context.request.get(base+'/api/projects/')).json()
let project=projects.find(p=>p.name===name)
if(!project){await request('projects/from_template/eh_datacenter?name='+encodeURIComponent(name)); projects=await (await context.request.get(base+'/api/projects/')).json();project=projects.find(p=>p.name===name)}
assert(project?.id)
await request('projects/'+project.id+'/activate')
await request('projects/'+project.id+'/lock')
const gens=await (await context.request.get(base+'/api/network/generators')).json()
const gen=gens.find(g=>String(g.carrier).includes('solar')||String(g.carrier).includes('pv'));assert(gen,'No PV fixture')
const buses=await (await context.request.get(base+'/api/network/buses')).json()
const bus=buses.find(b=>b.name===gen.bus)
await request('network/buses/'+encodeURIComponent(bus.name),{...bus,x:5.1,y:52.1},'put')
await request('projects/'+encodeURIComponent(name)+'/profiles/weather',{source:'synthetic',year:2025},'put')
await request('projects/'+encodeURIComponent(name))
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
page.setDefaultTimeout(20000)
await page.goto(base+'/app?project='+project.id,{waitUntil:'domcontentloaded',timeout:120000})
await page.getByTestId('ui-mode-expert').waitFor()
await page.locator('[role=tab][aria-selected=true]').filter({hasText:name}).waitFor()
await page.getByTestId('ui-mode-expert').click()
await page.evaluate(async name=>{const nav=await import('/src/layout/flowNavigation.ts');nav.openTimeSeriesFor('Generator',name)},gen.name)
await page.getByRole('button',{name:/^Renewables/}).click(); await page.getByTestId('tune-'+gen.name).waitFor()
await page.screenshot({path:dir+'/g-expert-renewables.png'})
await page.getByTestId('tune-'+gen.name).click()
await page.getByLabel('Preset',{exact:true}).waitFor()
await page.screenshot({path:dir+'/g-tune-untouched.png'})
console.log(JSON.stringify({project:project.id,generator:gen.name,errors,buttons:await page.getByRole('dialog').getByRole('button').allTextContents()}))
await context.storageState({path:dir+'/browser-session.json'})
await writeFile(dir+'/browser-fixture.json',JSON.stringify({project:project.id,name,generator:gen.name}))
await browser.close()
