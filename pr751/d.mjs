import { open, OUT } from './lib.mjs'
const tag = process.argv[2]
const { b, page } = await open('guided')
await page.keyboard.press('Control+j')
for (const sec of ['assets', 'commercial']) {
  await page.evaluate(async s => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setActiveSection(s) }, sec)
  await page.waitForTimeout(1500)
  const sel = sec === 'assets' ? '[data-testid=section-item-open],[data-testid=readiness-open]' : '[data-testid=section-open-expert-commercial]'
  const els = page.locator(sel + ',[data-testid^=section-open-expert-]')
  console.log(sec, await els.count())
  const first = els.first()
  if (await first.count()) { await first.scrollIntoViewIfNeeded(); const r = await first.boundingBox(); console.log(JSON.stringify(r)); await page.screenshot({ path: `${OUT}/guided-d-${sec}-${tag}.png`, clip: { x: Math.max(0, r.x - 250), y: Math.max(0, r.y - 60), width: 560, height: 130 } }) }
  else await page.screenshot({ path: `${OUT}/guided-d-${sec}-${tag}-miss.png` })
}
await b.close()
