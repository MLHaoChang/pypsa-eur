const {chromium}=require('playwright');
const fs=require('fs');const FX='/home/user/pypsa-eur/pypsa-gui/frontend/src/pages/decision/__fixtures__/';
const J=n=>JSON.parse(fs.readFileSync(FX+n+'.json','utf8'));
const study=J('study'),ledger=J('ledger'),run=J('run'),findings=J('findings'),tornado=J('tornado'),library=J('library'),report=J('report');
const P='proj1',S=study.study_id;study.base_project_name=P;
const V={
 rerun:{stale:true,stale_reasons:['intake_changed_since_findings'],verdict:{state:'stale',broken:[{edge:'input:study:s1/intake',field:null,recorded:'a',current_field:null,current:'b',reason:'changed',needs_run:true}]}},
 reassemble:{stale:true,stale_reasons:['report_has_no_findings_hashes'],verdict:{state:'stale',broken:[{edge:'input:report:s1/findings_hashes',field:null,recorded:'a',current_field:null,current:'b',reason:'changed',needs_run:false}]}}};
async function clipOf(l,pad=40){const bb=await l.boundingBox();const x=Math.max(0,bb.x-pad-300),y=Math.max(0,bb.y-pad-120);return {x,y,width:Math.min(1440-x,bb.width+2*pad+600),height:Math.min(900-y,bb.height+2*pad+260)}}
(async()=>{const b=await chromium.launch();
for(const mode of ['guided','expert'])for(const v of Object.keys(V)){
 const ctx=await b.newContext({viewport:{width:1440,height:900},deviceScaleFactor:2});
 await ctx.addInitScript(([m,ref])=>{try{localStorage.setItem('network-diagram:ui-mode',m);localStorage.setItem('network-diagram:ui-mode-explicit','1');localStorage.setItem('network-diagram:decision-study',ref)}catch{}},[mode,JSON.stringify({project:P,studyId:S})]);
 const p=await ctx.newPage();
 const j=(r,o,s=200)=>r.fulfill({status:s,contentType:'application/json',body:JSON.stringify(o)});
 await p.route(/\/api\/projects\/proj1\/studies/,r=>{const u=new URL(r.request().url()).pathname.replace(/\/$/,'');
  if(u.endsWith('/studies'))return j(r,[study]);
  if(u.endsWith('/library'))return j(r,library);
  if(u.endsWith('/ledger'))return j(r,ledger);
  if(u.endsWith('/run'))return j(r,run);
  if(u.endsWith('/tornado'))return j(r,tornado);
  if(u.endsWith('/findings'))return j(r,findings);
  if(u.endsWith('/report'))return j(r,{...report,...V[v]});
  if(u.endsWith(S))return j(r,study);
  return r.continue()});
 await p.goto('http://127.0.0.1:5173/projects');await p.waitForTimeout(2500);
 const btn=p.getByTestId('decision-studies-entry').getByRole('button').first();
 await btn.click();await p.waitForTimeout(2500);
 if(mode==='guided'){const t=p.getByText(/report/i).filter({hasText:/./});
  const n=p.locator('[data-testid="decision-study"] button, [data-testid="decision-study"] a').filter({hasText:/report/i});
  console.log('rail',await n.allInnerTexts());await n.first().click();}
 else await p.getByRole('navigation',{name:'Decision study sections'}).getByRole('button',{name:'Report'}).click();
 await p.waitForTimeout(1500);
 const ban=p.getByTestId('report-stale');
 console.log(mode,v,await ban.count(),JSON.stringify(await ban.innerText()));
 const card=ban.locator('xpath=ancestor::*[contains(@class,"rounded")][1]');
 await p.screenshot({path:`report-${v}-${mode}.png`,clip:await clipOf(ban)});
 await ctx.close();}
await b.close()})()
