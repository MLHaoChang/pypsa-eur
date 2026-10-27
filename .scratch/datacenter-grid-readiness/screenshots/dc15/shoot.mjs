import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const mode = process.argv[2], out = process.argv[3]
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1800, height: 1500 } })
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
// Guided has no grid-code draft view: it opens this Expert section (openSectionInExpert),
// so the draft shots are Expert only. Guided's DC 15 view is the results row (shoot_results.mjs).
if (mode !== 'expert') throw new Error('draft shots are Expert only; use shoot_results.mjs for Guided')
await p.getByText('Grid connection', { exact: true }).first().click(); await p.waitForTimeout(4000)
const shotRows = async (name) => {
  // The table scrolls inside its panel, so clip the viewport around the rows that
  // matter: the DC 15 rows, or (a blank draft, which has none) the table's last rows.
  const rows = p.locator('[data-testid="limit-q_capability_unit"], [data-testid="limit-voltage_deadband_pct"]')
  const n = await rows.count()
  const all = p.locator('[data-testid="limit-q_range_demand"]').locator('xpath=ancestor::tbody[1]/tr')
  const first = n ? rows.first() : all.nth(Math.max(0, (await all.count()) - 3))
  const last = n ? rows.last() : all.last()
  await first.evaluate(e => e.scrollIntoView({ block: "start" })); await p.waitForTimeout(500)
  const top = (await first.boundingBox()).y, end = await last.boundingBox()
  const table = await all.first().locator('xpath=ancestor::table[1]').boundingBox()
  const y = Math.max(0, top - 10)
  await p.screenshot({ path: `${out}/${name}-${mode}.png`,
    clip: { x: Math.max(0, table.x - 8), y, width: table.width + 16, height: Math.min(1500 - y, end.y + end.height + 100 - y) } })
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
