// Runs the (already shrunk, reserve 100 %) solve from the UI and shoots the Issues-panel failure card.
// Needs: node shoot.js (project + state.json), node shrink.js, then solve.js 1.0 has set reserve_share_load=1.
const { chromium } = require('playwright')
;(async () => {
  const browser = await chromium.launch()
  for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: __dirname + '/state.json', viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
    await ctx.addInitScript(t => { localStorage.setItem('network-diagram:theme', t); localStorage.setItem('network-diagram:theme-schema', '2') }, theme)
    const page = await ctx.newPage()
    await page.goto('http://127.0.0.1:5173/')
    await page.waitForTimeout(3000)
    await page.getByText('or-shots', { exact: true }).first().click({ force: true })
    await page.waitForTimeout(5000)
    await page.getByText('Expert', { exact: true }).first().click().catch(() => {}); await page.waitForTimeout(2500)
    await page.getByText('Run LOPF').first().click()
    await page.waitForTimeout(1500)
    const go = page.getByRole('button', { name: /^Run/ }).last()
    if (await go.isVisible().catch(() => false)) await go.click().catch(() => {})
    const hint = page.getByTestId('failure-constraint-hint').first()
    const log = page.getByText(/Operating reserve/).first()
    // the failure card lives in the Issues panel; open it as soon as the solve ends
    for (let i = 0; i < 60 && !(await hint.isVisible().catch(() => false)); i++) {
      await page.waitForTimeout(3000)
      await page.getByText('Issues', { exact: true }).first().click({ force: true }).catch(() => {})
    }
    await page.waitForTimeout(800)
    console.log(theme, 'hint visible', await hint.isVisible().catch(() => false))
    if (await hint.isVisible().catch(() => false)) {
      console.log(theme, 'HINT:', (await hint.innerText()).replace(/\s+/g, ' '))
      await hint.scrollIntoViewIfNeeded()
    }
    await page.screenshot({ path: `${__dirname}/${theme}-infeasible.png` })
    await ctx.close()
  }
  await browser.close()
})()
