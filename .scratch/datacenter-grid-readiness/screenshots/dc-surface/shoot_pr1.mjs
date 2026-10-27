import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const out = process.argv[2]
fs.mkdirSync(out, { recursive: true })
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1800, height: 1500 } })
await ctx.addInitScript(() => {
  localStorage.setItem('network-diagram:ui-mode', 'expert'); localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  localStorage.setItem('network-diagram:current-project', 'dcsurf-demo')
})
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(6000)
if (await p.locator('input[type=password]').count()) {
  await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com')
  await p.locator('input[type=password]').fill(pw); await p.keyboard.press('Enter'); await p.waitForTimeout(3000)
}
await p.getByText('Resume project').first().click(); await p.waitForTimeout(5000)
// Guided has no grid-code draft view: it opens this Expert section (openSectionInExpert), the DC 15 precedent.
await p.getByText('Grid connection', { exact: true }).first().click(); await p.waitForTimeout(4000)
const paths = ['harmonic_resonance_clearance_h', 'unbalance_limit_pct', 'current_tdd_standard']
const shotRows = async (name) => {
  const rows = p.locator(paths.map(x => `[data-testid="limit-${x}"]`).join(', '))
  const first = rows.first(), last = rows.last()
  await first.evaluate(e => e.scrollIntoView({ block: 'start' })); await p.waitForTimeout(500)
  const all = p.locator('[data-testid="limit-q_range_demand"]').locator('xpath=ancestor::tbody[1]/tr')
  const table = await all.first().locator('xpath=ancestor::table[1]').boundingBox()
  const top = (await first.boundingBox()).y, end = await last.boundingBox()
  const y = Math.max(0, top - 10)
  await p.screenshot({ path: `${out}/${name}-expert.png`,
    clip: { x: Math.max(0, table.x - 8), y, width: table.width + 16, height: Math.min(1500 - y, end.y + end.height + 100 - y) } })
  console.log(name, 'rows', await rows.count())
}
await p.getByRole('button', { name: 'Review draft pq_demo' }).click(); await p.waitForTimeout(2500)
await shotRows('01-dc12-limits-unconfirmed')
for (const path of paths) {
  await p.locator(`[data-testid="limit-${path}"]`).getByRole('button', { name: /^Confirm/ }).click(); await p.waitForTimeout(1500)
}
await shotRows('02-dc12-limits-confirmed')
await b.close()
