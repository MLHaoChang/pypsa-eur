const {chromium}=require('playwright');
const STUDY={status:'done',study:'investment_case',report:{present:true,stale:true,changed:['finance'],reason:'inputs_changed',verdict:{state:'stale',broken:[{edge:'input:investment_case:part/finance',field:'finance',recorded:'a',current_field:'finance',current:'b',reason:'changed',needs_run:true}]}}};
const REPORT={case_id:'investment_case',assumptions_hash:'abcdef0123456789',packs:{},project_irr_pre_tax:0.085,project_irr_post_tax:null,npv_at_wacc:1234567.8,lcoe_finance_consistent_eur_per_mwh:55.5,ppa_price_for_target_irr_eur_per_mwh:null,min_dscr:null,avg_dscr:null,llcr:null,plcr:null,wacc_vs_discount_rate_consistent:null,
completeness:{design:'skipped',commercial:'skipped',dispatch_modes:'skipped',participants:'ok',project:'ok',debt:'skipped',tax:'skipped',tax_equity:'skipped',uncertainty:'skipped',gates:'skipped'},
sections:{project:{status:'ok',payload:{years:[2030,2031],equity_post_tax_irr:null,equity_pre_tax_irr:0.14,equity_post_tax_npv:null,lifecycle_npv:-5000000,payback_years:7.25,solved_ppa_price:null,solve_ppa_status:null,has_counterfactual:false,incremental_net:[0,100],counterfactual_net:[0,0],flags:[]}}},cashflow_lines:[]};
exports.mock=async(p)=>{const j=(r,o)=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(o)});
 await p.route(/\/api\/results\/investment_case\/report(\?.*)?$/,r=>j(r,REPORT));
 await p.route(/\/api\/results\/investment_case(\?.*)?$/,r=>r.request().method()==='GET'?j(r,STUDY):r.continue());};
if(require.main===module)(async()=>{const b=await chromium.launch();
 const ctx=await b.newContext({viewport:{width:1440,height:900},deviceScaleFactor:2});
 await ctx.addInitScript(()=>{try{localStorage.setItem('network-diagram:ui-mode','expert');localStorage.setItem('network-diagram:ui-mode-explicit','1')}catch{}});
 const p=await ctx.newPage();await exports.mock(p);
 await p.goto('http://127.0.0.1:5173/app');await p.waitForTimeout(2500);
 await p.getByRole('button',{name:/^Results$/}).first().click();await p.waitForTimeout(1500);
 await p.getByText('Commercial & finance',{exact:true}).first().click();await p.waitForTimeout(1500);console.log((await p.locator('button').allInnerTexts()).filter(x=>/invest|case/i.test(x)).join('|'));
 console.log((await p.locator('button').allInnerTexts()).filter(x=>/invest|econ/i.test(x)).join('|'));
 await p.screenshot({path:'probe.png'});await b.close()})()
