import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const mode = process.argv[2], out = process.argv[3]
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 } })
await ctx.addInitScript(([m]) => {
  localStorage.setItem('network-diagram:ui-mode', m); localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  localStorage.setItem('network-diagram:current-project', 'dc15-demo')
}, [mode])
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(6000)
if (await p.locator('input[type=password]').count()) {
  await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com')
  await p.locator('input[type=password]').fill(pw); await p.keyboard.press('Enter'); await p.waitForTimeout(3000)
}
await p.getByText('Resume project').first().click(); await p.waitForTimeout(5000)
// The results row: the deadband in the "Needs an RMS study" line (run run_study.py first).
if (mode === 'guided') {
  await p.getByRole('button', { name: /Results & reports/ }).first().click(); await p.waitForTimeout(4000)
  await p.getByRole('button', { name: 'Open the grid checks' }).first().click(); await p.waitForTimeout(4000)
} else {
  await p.getByRole('button', { name: 'Grid study results' }).first().click(); await p.waitForTimeout(4000)
}
const rms = p.locator('[data-testid="compliance-rms"]')
console.log(mode, 'rms', await rms.count(), (await rms.allInnerTexts()).join(' / '))
// Master (the "before") has no RMS line: anchor on the PCC band note under the same table.
const anchor = (await rms.count()) ? rms : p.getByText(/^PCC reactive band/).first()
const sec = anchor.locator('xpath=..')
await sec.scrollIntoViewIfNeeded(); await p.waitForTimeout(500)
await sec.screenshot({ path: `${out}/04-results-${mode}.png` })
await b.close()
