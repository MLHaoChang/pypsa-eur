// homer-parity 01b-2 UX shots: Solver settings > Off-grid constraints > Operating reserve.
// NODE_PATH=/opt/node-tools/node_modules node shoot.js  (backend :8000, Vite :5173)
const { chromium, request } = require('playwright')
const fs = require('fs')
const BASE = 'http://127.0.0.1:5173'
const OUT = __dirname
;(async () => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
  const api = await request.newContext({ baseURL: BASE })
  let r = await api.post('/api/auth/login', { data: { email: 'admin@example.com', password: pw } })
  console.log('login', r.status())
  const st = await api.storageState()
  const h = { 'X-CSRF-Token': st.cookies.find(c => c.name === 'pypsa_gui_csrf')?.value }
  r = await api.post('/api/projects/from_template/eh_microgrid?name=or-shots', { headers: h })
  console.log('project', r.status())
  r = await api.post('/api/projects/or-shots/activate', { headers: h })
  console.log('activate', r.status())
  await api.storageState({ path: OUT + '/state.json' })
  const browser = await chromium.launch()
  for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ storageState: OUT + '/state.json', viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
    await ctx.addInitScript(t => {
      localStorage.setItem('network-diagram:theme', t)
      localStorage.setItem('network-diagram:theme-schema', '2')
    }, theme)
    const page = await ctx.newPage()
    await page.goto(BASE + '/')
    await page.waitForTimeout(3000)
    await page.getByText('or-shots', { exact: true }).first().click({ force: true })
    await page.waitForTimeout(5000)
    await page.getByText('Expert', { exact: true }).first().click().catch(() => {})
    await page.evaluate(async () => {
      const m = await import('/src/store/uiStore.ts')
      m.useUIStore.getState().setSlidePanel('simparams')
    })
    await page.waitForTimeout(2500)
    await page.getByRole('tab', { name: 'Network' }).click().catch(async () => {
      await page.getByText('Network', { exact: true }).first().click()
    })
    await page.waitForTimeout(1500)
    const block = page.getByTestId('offgrid-constraints')
    await block.scrollIntoViewIfNeeded()
    const f = l => block.getByLabel(l, { exact: true })
    const fill = async (a, b, c) => { await f('Reserve for load (%)').fill(a); await f('Reserve for solar output (%)').fill(b); await f('Reserve for wind output (%)').fill(c) }
    const shot = async tag => { await page.waitForTimeout(300); await block.screenshot({ path: `${OUT}/${theme}-${tag}.png` }) }
    await fill('', '', '')
    await shot('idle')
    await fill('10', '20', '15')
    await shot('set')
    await fill('10', '150', '15')
    await page.waitForTimeout(300)
    const bb = await block.boundingBox()
    // the panel column from the block's top down to the Save bar, so the disabled Save shows
    await page.screenshot({ path: `${OUT}/${theme}-out-of-range.png`, clip: { x: 820, y: Math.max(0, bb.y - 10), width: 620, height: 900 - Math.max(0, bb.y - 10) } })
    const save = page.getByRole('button', { name: 'Save settings' })
    console.log(theme, 'save disabled:', await save.isDisabled())
    await ctx.close()
  }
  await browser.close()
})()
