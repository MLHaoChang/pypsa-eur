// homer-parity 02b: Generator form shots. node shoot.js (after setup.js)
const { chromium } = require('playwright')
const OUT = __dirname
const BASE = 'http://127.0.0.1:5173'
;(async () => {
  const browser = await chromium.launch()
  const shootTheme = async theme => {
    const ctx = await browser.newContext({ storageState: OUT + '/state.json', viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2 })
    await ctx.addInitScript(t => { localStorage.setItem('network-diagram:theme', t); localStorage.setItem('network-diagram:theme-schema', '2') }, theme)
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
    const scrollTo = async text => {
      const el = page.getByText(new RegExp('^' + text + '\\b')).first()
      await el.scrollIntoViewIfNeeded()
      await el.evaluate(e => { let p = e; while (p && p.scrollHeight <= p.clientHeight + 5) p = p.parentElement; if (p) p.scrollTop += e.getBoundingClientRect().top - p.getBoundingClientRect().top - 300 })
      await page.waitForTimeout(500)
    }
    const snap = (f, c = clip) => page.screenshot({ path: `${OUT}/${theme}-${f}.png`, clip: c })
    await page.goto(BASE + '/'); await page.waitForTimeout(3000)
    await page.getByText('rh-shots', { exact: true }).first().click({ force: true }); await page.waitForTimeout(5000)
    await page.getByText('Expert', { exact: true }).first().click().catch(() => {}); await page.waitForTimeout(2000)
    await select('diesel-02b')
    await page.getByText('Edit Generator').click(); await page.waitForTimeout(1500)
    await scrollTo('Fuel price'); await snap('variable-om')
    await scrollTo('Lifetime in running hours'); await snap('investment-lifetime-hours')
    await page.getByRole('button', { name: 'Cancel' }).click(); await page.waitForTimeout(800)
    if (theme === 'light') {
      await select('diesel-uc-off')
      await page.getByText('Edit Generator').click(); await page.waitForTimeout(1500)
      await scrollTo('Fuel price'); await snap('form-uc-off')
      const inp = page.getByText('O&M per running hour', { exact: false }).first().locator('xpath=ancestor::*[.//input][1]').locator('input').first()
      await inp.fill('3'); await page.waitForTimeout(300)
      await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.waitForTimeout(800)
      await page.screenshot({ path: `${OUT}/light-refusal-om-uc-off.png`, clip: { x: 300, y: 0, width: 1140, height: 1100 } })
      await page.getByRole('button', { name: 'Cancel' }).click().catch(() => {}); await page.waitForTimeout(800)
      await page.getByText('Guided', { exact: true }).first().click(); await page.waitForTimeout(2500)
      await page.locator('text=diesel-02b').last().locator('xpath=ancestor::*[.//text()[contains(.,"Edit")]][1]').getByText('Edit', { exact: false }).last().click().catch(e => console.log('guided edit click failed'))
      await page.waitForTimeout(2500)
      await page.screenshot({ path: `${OUT}/guided-light.png` })
    }
    await ctx.close()
  }
  await shootTheme('light'); await shootTheme('dark')
  await browser.close()
})()
