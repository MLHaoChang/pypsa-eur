// Walk bugs 05/06 shots: the intent comparison on the Data Center Energy Hub template, light and dark.
// Run: NODE_PATH=$(npm root -g) node shoot.js <stage>   (stage: explore | shots)
const { chromium } = require('playwright'); const fs = require('fs')
const OUT = __dirname + '/shots/'; const API = 'http://127.0.0.1:8000/api'; const APP = 'http://127.0.0.1:5173/'
const PW = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const NAME = 'DC Intent Walk'
;(async () => {
  const b = await chromium.launch()
  for (const theme of process.argv[3] ? [process.argv[3]] : ['light', 'dark']) {
    const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 }, colorScheme: theme })
    const p = await ctx.newPage()
    const lg = await p.request.post(API + '/auth/login', { data: { email: 'admin@example.com', password: PW } })
    const H = { 'X-CSRF-Token': (await lg.json()).csrf_token }
    if (theme === 'light' || process.argv[3]) {
      const r = await p.request.post(API + '/projects/from_template/eh_datacenter?name=' + encodeURIComponent(NAME), { data: { dc_load: {} }, headers: H })
      console.log('create', r.status())
    }
    await p.goto(APP); await p.waitForTimeout(3000)
    if (await p.getByRole('button', { name: 'Sign in' }).count()) {
      await p.getByRole('button', { name: 'Sign in' }).click()
      await p.fill('input[type=email]', 'admin@example.com'); await p.fill('input[type=password]', PW)
      await p.keyboard.press('Enter'); await p.waitForTimeout(6000)
    }
    await p.locator('button,a,[role=button]', { hasText: new RegExp('Open ' + NAME) }).first().dispatchEvent('click', {}, { timeout: 5000 }).catch(() => {})
    await p.waitForTimeout(4000)
    await p.getByRole('button', { name: 'Expert', exact: true }).click().catch(() => {}); await p.waitForTimeout(1500)
        if (process.argv[2] === 'explore') { await p.screenshot({ path: OUT + 'explore-' + theme + '.png' }); console.log(await p.locator('body').innerText().then(t => t.slice(0, 1500))); continue }
    if (theme === 'dark') { await p.getByRole('button', { name: /Dark theme/ }).click().catch(() => {}); await p.waitForTimeout(800) }
    const gs = p.getByText('Grid study', { exact: true })
    if (!(await gs.count())) { await p.getByText(/Scenarios & optimisation/i).first().click(); await p.waitForTimeout(1500) }
    await p.getByText('Grid study', { exact: true }).first().scrollIntoViewIfNeeded()
    await p.getByText('Grid study', { exact: true }).first().click(); await p.waitForTimeout(3000)
    const sec = p.getByTestId('intent-poc')
    await sec.scrollIntoViewIfNeeded(); await p.waitForTimeout(2500)
    const box = p.locator('section,div').filter({ has: p.getByTestId('intent-run') }).last()
    await p.getByTestId('intent-in-ramp').scrollIntoViewIfNeeded()
    await p.screenshot({ path: OUT + `prefilled-${theme}.png` })
    await p.getByTestId('intent-pq-x_pu').fill('0.08'); await p.waitForTimeout(500)
    await p.getByTestId('intent-in-library').fill('40,63,80'); await p.getByTestId('intent-in-pst').fill(await p.getByTestId('intent-in-pst').inputValue() || '1')
    await p.getByTestId('intent-pq-x_pu').scrollIntoViewIfNeeded()
    await p.screenshot({ path: OUT + `edited-${theme}.png` })
    await p.getByLabel(/^B\. /).check(); await p.getByLabel(/^C\. /).check()
    await p.getByTestId('intent-run').click(); await p.waitForTimeout(4000)
    await p.getByTestId('intent-run').scrollIntoViewIfNeeded()
    await p.screenshot({ path: OUT + `refusal-${theme}.png` })
    console.log(theme, 'error:', await p.getByTestId('intent-error').innerText().catch(() => '(none)'))
    await ctx.close()
  }
  await b.close()
})().catch(e => { console.error(e); process.exit(1) })
