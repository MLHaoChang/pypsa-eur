const { chromium } = require('playwright')
const [,, project, tag, ...flags] = process.argv
const has = f => flags.includes(f)
;(async () => {
  const browser = await chromium.launch()
  const width = has('narrow') ? 1100 : 1440
  const ctx = await browser.newContext({ storageState: 'state.json', viewport: { width, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  if (has('slow')) await page.route('**/api/results/headline_kpis', async r => { await new Promise(x => setTimeout(x, 6000)); r.continue() })
  if (has('stale')) await page.route('**/api/simulation/status', async r => { const res = await r.fetch(); const j = await res.json(); j.dispatch = 'stale'; r.fulfill({ response: res, json: j }) })
  if (has('fail')) await page.route('**/api/results/headline_kpis', r => r.fulfill({ status: 500, body: '{}' }))
  await page.goto('http://127.0.0.1:5173/')
  const card = page.locator(`text=${project}`).first()
  await card.waitFor({ timeout: 20000 })
  await page.locator('a,button,[role=button]').filter({ hasText: project }).first().click()
  await page.waitForTimeout(2500)
  if (has('run')) {
    const csrf = (await ctx.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
    await page.request.post('http://127.0.0.1:5173/api/simulation/run', { headers: { 'X-CSRF-Token': csrf }, data: {} })
    for (let i = 0; i < 60; i++) { await page.waitForTimeout(2000); const s = await (await page.request.get('http://127.0.0.1:5173/api/simulation/status')).json(); if (!s.running) break }
    await page.reload(); await page.waitForTimeout(2500)
  }
  const mode = has('guided') ? 'Guided' : 'Expert'
  await page.getByText(mode, { exact: true }).first().click().catch(() => {})
  await page.waitForTimeout(800)
  if (has('light')) { await page.mouse.click(61, 853); await page.waitForTimeout(400) }
  if (!has('guided')) {
    const res = page.getByText('Results', { exact: true }).first()
    await res.scrollIntoViewIfNeeded(); await res.click()
  }
  await page.waitForTimeout(has('slow') ? 1200 : 3500)
  await page.screenshot({ path: `shots/${tag}.png` })
  console.log(tag, 'strip:', await page.locator('[data-testid="headline-strip"]').count(), 'title:', await page.title())
  await browser.close()
})()
