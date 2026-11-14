const {chromium}=require('playwright');const fs=require('fs');
const FX=JSON.parse(fs.readFileSync(process.argv[2]));
const j=(r,o)=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(o)});
const REAL=JSON.parse(require('child_process').execSync('curl -s "http://127.0.0.1:8000/api/projects/Renewables%20IPP%20with%20battery/flow"'));
const flow=by=>{const f=JSON.parse(JSON.stringify(REAL));f.deferred=false;f.run_request={...(f.run_request||{}),optimisation:true,study:true,loadflow:false};
 const s=f.sections.find(x=>x.section==='results');s.freshness_by_kind=by;return f};
(async()=>{const b=await chromium.launch();
 for(const [name,by] of [['with-notice',{optimisation:'uncompared',study:'fresh',loadflow:'fresh'}],['without-notice',{optimisation:'fresh',study:'fresh',loadflow:'fresh'}]]){
 const ctx=await b.newContext({viewport:{width:1440,height:900},deviceScaleFactor:2});
 await ctx.addInitScript(()=>{try{localStorage.setItem('network-diagram:ui-mode','guided');localStorage.setItem('network-diagram:ui-mode-explicit','1')}catch{}});
 const p=await ctx.newPage();p.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text().slice(0,500))});p.on('pageerror',e=>console.log('PAGEERR',e.stack.split('\n').slice(0,3).join(' | ')));
 await p.route(/\/api\/projects\/[^/]+\/flow(\?.*)?$/,r=>j(r,flow(by)));
 await p.route(/\/api\/results\/eh_study(\?.*)?$/,r=>j(r,{status:'done',archetype:'weak_flexible',report:FX.report}));
 await p.route(/\/api\/results\/eh_review(\?.*)?$/,r=>j(r,FX.review));
 await p.goto('http://127.0.0.1:5173/app');await p.waitForTimeout(2500);
 await p.getByRole('button',{name:/Hub design/}).first().click();await p.waitForTimeout(1500);
 await p.getByRole('button',{name:/Renewables IPP with battery/}).first().click();await p.waitForTimeout(2000);
 const ob=p.getByRole('button',{name:'Open the existing project'});if(await ob.count())await ob.click();await p.waitForTimeout(6000);
 await p.screenshot({path:'dbg.png'});await p.getByTestId('hub-rail-step-results').click({timeout:8000}).catch(e=>console.log('rail missing'));await p.waitForTimeout(2500);
 await p.getByText(/already exists/).first().waitFor({state:'detached',timeout:30000}).catch(()=>{});
 console.log(name,'notice',await p.getByTestId('hub-results-unchecked').count(),'stale',await p.getByTestId('hub-results-stale').count());
 await p.screenshot({path:`results-${name}.png`});await ctx.close()}
await b.close()})()
