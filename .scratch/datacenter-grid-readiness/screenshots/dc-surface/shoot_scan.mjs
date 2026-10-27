import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const mode = process.argv[2], out = process.argv[3], name = process.argv[4] || '01'
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 } })
await ctx.addInitScript(([m]) => {
  localStorage.setItem('network-diagram:ui-mode', m); localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  localStorage.setItem('network-diagram:current-project', 'dcsurf-demo')
}, [mode])
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(6000)
if (await p.locator('input[type=password]').count()) {
  await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com')
  await p.locator('input[type=password]').fill(pw); await p.keyboard.press('Enter'); await p.waitForTimeout(3000)
}
await p.getByText('Resume project').first().click(); await p.waitForTimeout(5000)
if (mode === 'guided') {
  await p.getByRole('button', { name: /Results & reports/ }).first().click(); await p.waitForTimeout(4000)
  await p.getByRole('button', { name: 'Open the grid checks' }).first().click(); await p.waitForTimeout(4000)
} else {
  await p.getByRole('button', { name: 'Grid study results' }).first().click(); await p.waitForTimeout(4000)
}
console.log(mode, 'ui-mode', await p.evaluate(() => localStorage.getItem('network-diagram:ui-mode')))
const stale = p.getByTestId('campus-results-stale')
if (await stale.count()) {
  await stale.scrollIntoViewIfNeeded(); await stale.screenshot({ path: `${out}/${name}-stale-${mode}.png` })
  console.log('stale:', await stale.innerText())
}
const anchor = p.locator('[data-testid="scan-chart"], [data-testid="scan-not-computed"], [data-testid="scan-error"]').first()
const sec = anchor.locator('xpath=ancestor::section[1]')
const target = (await sec.count()) ? sec : anchor.locator('xpath=../..')
await target.scrollIntoViewIfNeeded(); await p.mouse.move(2, 2); await p.waitForTimeout(800)
await target.screenshot({ path: `${out}/${name}-scan-${mode}.png` })
await b.close()
