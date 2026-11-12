// ui-modern 03 S3 shots. Run from s3work/ (needs shots/) with API on :8000 and Vite on :5173.
//   node ../s3_shoot.js            all shots;  node ../s3_shoot.js <tag-substring>  some
const { chromium, request } = require('playwright')
const fs = require('fs')
const BASE = 'http://127.0.0.1:5173'
const SHOTS = [
  ['Alpha site', 'um07-base-expert-dark-after'],
  ['Alpha more rooftop PV', 'um07-case-expert-dark-after', 'base=Alpha site'],
]
;(async () => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
  const api = await request.newContext({ baseURL: BASE })
  console.log('login', (await api.post('/api/auth/login', { data: { email: 'admin@example.com', password: pw } })).status())
  await api.storageState({ path: 'state.json' })
  const browser = await chromium.launch()
  for (const [project, tag, ...flags] of SHOTS) {
    if (process.argv[2] && !tag.includes(process.argv[2])) continue
    const has = f => flags.includes(f)
    const ctx = await browser.newContext({ storageState: 'state.json', viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
    const page = await ctx.newPage()
    let api_json = null
    // The working-tree backend only compares on `?compare=1`; the frontend on disk does not ask for it yet.
    // S3_COMPARE=1 adds the parameter to the frontend's own request (the response is the backend's, untouched).
    if (process.env.S3_COMPARE) await page.route('**/api/results/headline_kpis*', r => { const u = new URL(r.request().url()); u.searchParams.set('compare', '1'); r.continue({ url: u.toString() }) })
    page.on('response', async r => { if (r.url().includes('/api/results/headline_kpis')) { try { api_json = r.status() + ' ' + await r.text() } catch (e) {} } })
    await page.goto(BASE + '/')
    // A sensitivity case is not on the projects page: open its base, activate the case, take the banner's switch.
    const base = (flags.find(f => f.startsWith('base=')) || '').slice(5)
    const first = base || project
    await page.locator(`text=${first}`).first().waitFor({ timeout: 20000 })
    await page.locator('a,button,[role=button]').filter({ hasText: first }).first().click()
    await page.waitForTimeout(3000)
    if (base) {
      const csrf = (await ctx.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
      const r = await page.request.post(BASE + '/api/projects/' + encodeURIComponent(project) + '/activate', { headers: { 'X-CSRF-Token': csrf } })
      if (r.status() !== 200) console.log('activate', r.status(), await r.text())
      await page.reload(); await page.waitForTimeout(3000)
      await page.getByText('Switch to ' + project).first().click(); await page.waitForTimeout(3500)
    }
    await page.getByText('Expert', { exact: true }).first().click().catch(() => {})
    await page.waitForTimeout(800)
    if (has('light')) { await page.mouse.click(61, 853); await page.waitForTimeout(400) }
    const res = page.getByText('Results', { exact: true }).first()
    await res.scrollIntoViewIfNeeded(); await res.click()
    await page.waitForTimeout(3500)
    if (has('guided')) { await page.getByText('Guided', { exact: true }).first().click().catch(e => console.log('guided click failed')); await page.waitForTimeout(3000) }
    const strip = page.locator('[data-testid="headline-strip"]')
    if (await strip.count()) await strip.first().scrollIntoViewIfNeeded()
    await page.screenshot({ path: `../${tag}.png` })
    console.log('\n==', tag, '| strips:', await strip.count())
    console.log('TEXT:', await strip.count() ? await strip.first().evaluate(e => e.innerText.replace(/\n+/g, ' | ')) : '(none)')
    if (await strip.count()) console.log('BOX:', JSON.stringify(await strip.first().boundingBox()), 'theme:', await page.evaluate(() => document.documentElement.className + ' ' + (document.documentElement.dataset.theme || '')))
    console.log('STATUS:', JSON.stringify(await (await page.request.get(BASE + '/api/simulation/status')).json()).slice(0, 400))
    fs.writeFileSync(`shots/${tag}.json`, api_json || 'null')
    await ctx.close()
  }
  await browser.close()
})()
