const {chromium}=require('playwright');const {mock}=require('./ic.js');
async function shot(p,loc,path){const bb=await loc.boundingBox();const x=Math.max(0,bb.x-250),y=Math.max(0,bb.y-160);
 await p.screenshot({path,clip:{x,y,width:Math.min(1440-x,bb.width+500),height:Math.min(900-y,bb.height+380)}})}
(async()=>{const b=await chromium.launch();
 for(const mode of ['guided','expert']){
 const ctx=await b.newContext({viewport:{width:1440,height:900},deviceScaleFactor:2});
 await ctx.addInitScript(m=>{try{localStorage.setItem('network-diagram:ui-mode',m);localStorage.setItem('network-diagram:ui-mode-explicit','1')}catch{}},mode);
 const p=await ctx.newPage();await mock(p);
 await p.goto('http://127.0.0.1:5173/app');await p.waitForTimeout(2500);
 if(mode==='guided'){
  await p.getByRole('button',{name:/Hub design/}).first().click();await p.waitForTimeout(1500);
  await p.getByRole('button',{name:/Renewables IPP with battery/}).first().click();await p.waitForTimeout(2000);
  const ob=p.getByRole('button',{name:'Open the existing project'});if(await ob.count())await ob.click();await p.waitForTimeout(6000);
  await p.getByTestId('hub-rail-step-results').click();await p.waitForTimeout(2500);
  await p.getByTestId('hub-chapter-investment-open').click();await p.waitForTimeout(2500);
  await p.getByText('Investment case',{exact:true}).last().click();await p.waitForTimeout(2500);
 } else {
  await p.getByRole('button',{name:/^Results$/}).first().click();await p.waitForTimeout(1200);
  await p.getByText('Commercial & finance',{exact:true}).first().click();await p.waitForTimeout(800);
  await p.getByText('Investment case',{exact:true}).last().click();await p.waitForTimeout(2500);
 }
 const st=p.getByTestId('ic-stale');console.log(mode,await st.count(),JSON.stringify(await st.allInnerTexts()));
 if(await st.count())await shot(p,st,`ic-stale-${mode}.png`);else await p.screenshot({path:`ic-fail-${mode}.png`});
 await ctx.close()}
await b.close()})()
