import { open, OUT } from './lib.mjs'
const tag = process.argv[2]
const { b, page } = await open('guided')

await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel(null); useUIStore.getState().setSelectedComponent({ type: 'Load', name: 'L1' }) })
await page.waitForTimeout(1200)
await page.screenshot({ path: `${OUT}/guided-c-selected-${tag}.png` })
await page.evaluate(async () => { const m = await import('/src/layout/flowNavigation.ts'); m.openFlowTargetInPlace({ section: 'assets', panel: 'assets', component: 'Generator', entity: 'PV', field: 'p_nom' }) })
await page.getByTestId('field-drawer').waitFor()
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/guided-c-drawer-${tag}.png` })
console.log(await page.evaluate(() => [...document.querySelectorAll('aside,[data-testid=field-drawer]')].map(e => { const r = e.getBoundingClientRect(); return (e.dataset.testid||e.tagName) + ':' + Math.round(r.x) + '-' + Math.round(r.right) + ' z' + getComputedStyle(e).zIndex }).join(' | ')))
await b.close()
