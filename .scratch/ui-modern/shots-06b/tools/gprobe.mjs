import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 }, storageState: process.argv[2] })
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: /ipphyb(?!\w)/ }).first().click(); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Guided', exact: true }).first().click(); await p.waitForTimeout(2500)


await p.getByTestId('hub-rail-step-results').first().click(); await p.waitForTimeout(3000)
console.log(await p.evaluate(() => [...document.querySelectorAll('[data-testid]')].map(e => e.dataset.testid).filter(t => /chapter|tab|results|flow|shape/i.test(t)).slice(0, 50)))
console.log(await p.evaluate(() => [...document.querySelectorAll('button,[role=tab]')].map(e => e.textContent.trim().slice(0, 25)).filter(t=>/flow|shape|load|result/i.test(t))))
await p.getByTestId('hub-chapter-operation-open').first().click(); await p.waitForTimeout(4000)
console.log(await p.evaluate(() => [...document.querySelectorAll('button,[role=tab]')].map(e => e.textContent.trim().slice(0, 25)).filter(t=>t).slice(0,80)))
console.log(await p.evaluate(() => [...document.querySelectorAll('[data-testid]')].map(e => e.dataset.testid).filter(t => /results-/i.test(t)).slice(0, 50)))
await b.close()
