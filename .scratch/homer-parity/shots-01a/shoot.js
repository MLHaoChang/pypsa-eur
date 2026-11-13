// homer-parity 01a UX shots (U5): Solver settings › Off-grid constraints.
// node shoot.js  (backend :8000, Vite :5173, admin dev password on disk)
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
  const csrf = st.cookies.find(c => c.name === 'pypsa_gui_csrf')?.value
  const h = { 'X-CSRF-Token': csrf }
  r = await api.post('/api/projects/from_template/eh_microgrid?name=rf-shots', { headers: h })
  console.log('project', r.status())
  r = await api.post('/api/projects/rf-shots/activate', { headers: h })
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
    await page.getByText('rf-shots', { exact: true }).first().click({ force: true })
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
    const input = block.locator('input')
    const shot = async tag => {
      await page.waitForTimeout(300)
      await block.screenshot({ path: `${OUT}/${theme}-${tag}.png` })
    }
    await input.fill('')
    await shot('idle')
    await input.fill('80')
    await shot('set')
    await input.fill('150')
    await shot('out-of-range')
    if (theme === 'light') await page.screenshot({ path: `${OUT}/light-page-context.png` })
    await ctx.close()
  }
  await browser.close()
})()
