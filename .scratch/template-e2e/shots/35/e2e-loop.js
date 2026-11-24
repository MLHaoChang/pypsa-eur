const { chromium } = require('playwright')
const fs = require('fs'); const os = require('os')
const pw = fs.readFileSync(os.homedir() + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const BASE = 'http://127.0.0.1:5173'
const SHOT = process.env.SHOT_DIR
;(async () => {
  const browser = await chromium.launch()
  const ctx0 = await browser.newContext()
  const login = await ctx0.request.post(BASE + '/api/auth/login', { data: { email: 'admin@example.com', password: pw } })
  const csrf = (await login.json()).csrf_token
  const hdr = { 'X-CSRF-Token': csrf }
  let list = await (await ctx0.request.get(BASE + '/api/projects/')).json()
  let proj = list.find(p => p.name === 'E2E Deep Link')
  if (!proj) {
    const r = await ctx0.request.post(BASE + '/api/projects/from_template/3bus?name=E2E%20Deep%20Link', { headers: hdr, data: null })
    console.log('create', r.status())
    list = await (await ctx0.request.get(BASE + '/api/projects/')).json()
    proj = list.find(p => p.name === 'E2E Deep Link')
  }
  const state = await ctx0.storageState()
  const N = +process.env.N || 20
  let ok = 0
  for (let i = 0; i < N; i++) {
    const ctx = await browser.newContext({ storageState: state, viewport: { width: 1280, height: 800 } })
    const page = await ctx.newPage()
    await page.route('**/api/auth/me', async route => { await new Promise(r => setTimeout(r, 200)); await route.continue() })
    await page.goto(`${BASE}/app?project=${proj.id}`)
    try {
      await page.getByText('E2E Deep Link').first().waitFor({ timeout: 30000 })
      const unnamed = await page.getByText('Unnamed Network').count()
      if (!unnamed) ok++
      console.log(i, 'ok', !unnamed, page.url().includes('project=') ? 'param-left' : 'param-stripped')
      if (SHOT && (i === 0 || i === N - 1)) await page.screenshot({ path: `${SHOT}/open-${i}.png` })
    } catch (e) {
      console.log(i, 'FAIL'); if (SHOT) await page.screenshot({ path: `${SHOT}/fail-${i}.png` })
    }
    await ctx.close()
  }
  console.log(`RESULT ${ok}/${N}`)
  await browser.close()
})()
