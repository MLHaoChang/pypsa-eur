const {chromium}=require('playwright');const {mock}=require('./ic.js');
(async()=>{const b=await chromium.launch();
 for(const mode of ['guided','expert']){
 const ctx=await b.newContext({viewport:{width:1440,height:900},deviceScaleFactor:2});
 await ctx.addInitScript(m=>{try{localStorage.setItem('network-diagram:ui-mode',m);localStorage.setItem('network-diagram:ui-mode-explicit','1')}catch{}},mode);
 const p=await ctx.newPage();await mock(p);p.on('request',r=>{if(/investment_case/.test(r.url()))console.log(r.method(),r.url())});
 await p.goto('http://127.0.0.1:5173/app');await p.waitForTimeout(2500);
 const hb=p.getByRole('button',{name:/Hub design/});console.log(mode,'hub btn',await hb.count());
 if(!await hb.count()){await ctx.close();continue}
 await hb.first().click();await p.waitForTimeout(1500);
 await p.getByRole('button',{name:/Renewables IPP with battery/}).first().click();await p.waitForTimeout(2000);
 const ob=p.getByRole('button',{name:'Open the existing project'});if(await ob.count())await ob.click();await p.waitForTimeout(6000);
 await p.getByTestId('hub-rail-step-results').click();await p.waitForTimeout(2500);
 const t=p.getByTestId('hub-chapter-investment');console.log(mode,'tile',await t.count());
 if(await t.count()){await t.scrollIntoViewIfNeeded();console.log(JSON.stringify(await t.innerText()));
  // let any open-project toast close before the shot (UX 21: no toast in the frame)
  await p.getByText(/already exists/).first().waitFor({state:'detached',timeout:30000}).catch(()=>{});
  await p.waitForTimeout(1000);
  console.log('toast left', await p.getByText(/already exists/).count());
  await p.screenshot({path:`hub-investment-tile-${mode}.png`});
  await p.getByTestId('hub-chapter-investment-open').click();await p.waitForTimeout(2500);
  const st=p.getByTestId('ic-stale');console.log(mode,'ic-stale after open',await st.count());
  await p.screenshot({path:`after-open-${mode}.png`});}
 await ctx.close()}
await b.close()})()
