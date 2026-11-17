// THEME-LIGHT-1 slice (a) real-app shots. Needs the API on :8000 and Vite on :5173.
//   node shoot.cjs <outdir>
// Run setup.py first (creates the theme-demo project). Per theme: the static sign-in page, then (signed in) the projects home and the
// workbench in Guided and Expert.
const { chromium } = require('playwright')
const fs = require('node:fs')
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const out = process.argv[2] ?? '.'
const BASE = 'http://127.0.0.1:5173'

;(async () => {
  const browser = await chromium.launch()
  for (const theme of ['light', 'dark']) {
    for (const mode of ['guided', 'expert']) {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
      await ctx.addInitScript(([t, m]) => {
        localStorage.setItem('network-diagram:theme-schema', '2')
        localStorage.setItem('network-diagram:theme', t)
        localStorage.setItem('network-diagram:ui-mode', m)
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      }, [theme, mode])
      const p = await ctx.newPage()
      await p.goto(BASE + '/')
      await p.waitForSelector('input[type=password]', { timeout: 20000 })
      await p.waitForTimeout(1500)
      if (mode === 'guided') await p.screenshot({ path: `${out}/signin-${theme}.png` })
      await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com')
      await p.locator('input[type=password]').fill(pw)
      await p.keyboard.press('Enter')
      await p.getByRole('navigation', { name: 'Account' }).waitFor({ timeout: 60000 })
      await p.getByText('theme-demo').first().waitFor({ timeout: 30000 })
      await p.waitForTimeout(1500)
      await p.screenshot({ path: `${out}/home-${mode}-${theme}.png` })
      await p.getByText('theme-demo').first().click({ force: true })
      await p.waitForTimeout(12000)
      console.log(mode, theme, p.url())
      await p.screenshot({ path: `${out}/workbench-${mode}-${theme}.png` })
      await ctx.close()
    }
  }
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
