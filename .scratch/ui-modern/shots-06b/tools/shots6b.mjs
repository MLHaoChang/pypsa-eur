import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [state, tag, out] = process.argv.slice(2)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: state })
const p = await ctx.newPage(); p.setDefaultTimeout(60000)
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: 'ipphyb' }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
await p.getByRole('button', { name: 'Results', exact: true }).first().click(); await p.waitForTimeout(3500)
const shot = async n => { await p.waitForTimeout(3500); await p.screenshot({ path: `/home/user/pypsa-eur/.scratch/ui-modern/shots-06b/after-${n}.png` }) }
const go = async (g, ct, v, n) => { try { await p.getByTestId('results-group-'+g).first().click(); await p.waitForTimeout(1500); await p.getByTestId('results-cardtab-'+ct).first().click(); await p.waitForTimeout(1500); if (v) await p.getByRole('button', { name: v, exact: true }).first().click(); await shot(n) } catch (e) { console.log('miss', n) } }
await go('grid','load_flow',null,'loadflow')
await go('capacity','operation','Lost load','lostload')
await go('capacity','operation','Storage cycling','reserve')
await go('capacity','operation','Energy flow','energyflows')
await go('capacity','operation','Load shape','loadshape')
await go('capacity','costs_prices',null,'costs')
await b.close()
