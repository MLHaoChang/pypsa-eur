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
await p.getByText('Grid connection', { exact: true }).first().click(); await p.waitForTimeout(4000)
const shotRows = async (name) => {
  const rows = p.locator('[data-testid="limit-q_capability_unit"], [data-testid="limit-voltage_deadband_pct"], [data-testid="limit-q_capability_pq"]')
  const n = await rows.count()
  const table = n ? rows.first().locator('xpath=ancestor::table[1]') : p.locator('[data-testid^="limit-"]').first().locator('xpath=ancestor::table[1]')
  await table.scrollIntoViewIfNeeded(); await p.waitForTimeout(500)
  await table.screenshot({ path: `${out}/${name}-${mode}.png` })
  console.log(name, mode, 'rows', n)
}
await p.getByRole('button', { name: 'Review draft extracted_demo' }).click(); await p.waitForTimeout(2500)
await shotRows('01-extracted-unconfirmed')
for (const path of ['q_capability_unit', 'voltage_deadband_pct']) {
  await p.locator(`[data-testid="limit-${path}"]`).getByRole('button', { name: /^Confirm/ }).click(); await p.waitForTimeout(1500)
}
await shotRows('02-confirmed')
await p.getByRole('button', { name: 'Review draft blank_demo' }).click(); await p.waitForTimeout(2500)
await shotRows('03-blank-unset')
await b.close()
