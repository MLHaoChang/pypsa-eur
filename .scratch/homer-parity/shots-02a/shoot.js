// homer-parity 02a: Generator form shots. node shoot.js (after setup.js)
const { chromium } = require('playwright')
const OUT = __dirname
const BASE = 'http://127.0.0.1:5173'
;(async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ storageState: OUT + '/state.json', viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2 })
  await ctx.addInitScript(() => { localStorage.setItem('network-diagram:theme', 'light'); localStorage.setItem('network-diagram:theme-schema', '2') })
  const page = await ctx.newPage()
  const select = async n => {
    await page.evaluate(async n => {
      const m = await import('/src/store/uiStore.ts')
      const s = m.useUIStore.getState()
      s.setSelectedComponent({ type: 'Generator', name: n }); s.openRightPanel()
    }, n)
    await page.waitForTimeout(2500)
  }
  const clip = { x: 1120, y: 0, width: 320, height: 1100 }
  // scroll the edit form so the cost / technical block is in view
  const scrollForm = async () => {
    await page.getByText('Variable cost', { exact: true }).first().scrollIntoViewIfNeeded()
    await page.getByText('Variable cost', { exact: true }).first().evaluate(e => { let p = e; while (p && p.scrollHeight <= p.clientHeight + 5) p = p.parentElement; if (p) p.scrollTop += e.getBoundingClientRect().top - 150 - p.getBoundingClientRect().top + 150 - 150 })
    await page.waitForTimeout(500)
  }
  const snap = f => page.screenshot({ path: `${OUT}/${f}.png`, clip })
  await page.goto(BASE + '/'); await page.waitForTimeout(3000)
  await page.getByText('fc-shots', { exact: true }).first().click({ force: true }); await page.waitForTimeout(5000)
  await page.getByText('Expert', { exact: true }).first().click().catch(() => {}); await page.waitForTimeout(2000)
  await select('diesel-plain')
  await page.getByText('Edit Generator').click(); await page.waitForTimeout(1500)
  await scrollForm(); await snap('1-expert-form-uc-off')
  await page.getByRole('button', { name: 'Cancel' }).click(); await page.waitForTimeout(800)
  await select('diesel-02a')
  await page.getByText('Edit Generator').click(); await page.waitForTimeout(1500)
  await scrollForm(); await snap('2-expert-form-uc-on-standby')
  await page.getByText('Unit commitment', { exact: true }).first().click(); await page.waitForTimeout(500)
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/3-refusal-no-load-uc-off.png`, clip: { x: 300, y: 0, width: 1140, height: 1100 } })
  await page.getByRole('button', { name: 'Cancel' }).click().catch(() => {}); await page.waitForTimeout(800)
  await page.getByText('Guided', { exact: true }).first().click(); await page.waitForTimeout(2500)
  await page.locator('text=diesel-02a').last().locator('xpath=ancestor::*[.//text()[contains(.,"Edit")]][1]').getByText('Edit', { exact: false }).last().click().catch(e => console.log('guided edit click failed'))
  await page.waitForTimeout(2500)
  await page.screenshot({ path: `${OUT}/4-guided.png` })
  await browser.close()
})()
