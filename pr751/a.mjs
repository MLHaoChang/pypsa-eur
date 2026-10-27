import { open, OUT } from './lib.mjs'
const tag = process.argv[2]
const { b, page } = await open('expert')
await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('settings') })
await page.waitForTimeout(1500)
const btn = page.getByTestId('mcp-add-open')
console.log(await btn.count())
await btn.scrollIntoViewIfNeeded()
const r = await btn.boundingBox()
const clip = { x: Math.max(0, r.x - 40), y: Math.max(0, r.y - 20), width: 400, height: 60 }
await page.screenshot({ path: `${OUT}/a-mcp-enabled-${tag}.png`, clip })
await btn.evaluate(e => { e.disabled = true })
await page.waitForTimeout(300)
await page.screenshot({ path: `${OUT}/a-mcp-disabled-${tag}.png`, clip })
console.log(await btn.evaluate(e => getComputedStyle(e).opacity + ' ' + getComputedStyle(e).cursor))
await b.close()
