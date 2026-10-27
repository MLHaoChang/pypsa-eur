// node shoot_scorecard.mjs <guided|expert> <out dir> <name> [row id ...]: the scorecard, clipped to the header and the rows named.
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const [mode, out, name, ...ids] = process.argv.slice(2)
fs.mkdirSync(out, { recursive: true })
const b = await chromium.launch()
const width = Number(process.env.WIDTH) || (mode === 'guided' ? 1280 : 1800)
const ctx = await b.newContext({ viewport: { width, height: 1500 } })
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
await p.getByText('Resume project').first().click(); await p.waitForTimeout(6000)
if (mode === 'guided') {
  const toggle = p.getByTestId('hub-site-grid-readiness-toggle')
  if (!(await toggle.count())) await p.getByTestId('hub-rail').getByText(/Site/).first().click()
  await p.waitForTimeout(2500)
  await toggle.click(); await p.waitForTimeout(2500)
} else {
  await p.getByRole('button', { name: /Issues/ }).first().click(); await p.waitForTimeout(3000)
  const more = p.getByText('Grid readiness', { exact: true }).first()
  if (await more.count()) { await more.click().catch(() => {}); await p.waitForTimeout(2000) }
}
await p.screenshot({ path: `${out}/${name}-${mode}-full.png` })
const card = p.getByTestId('grid-readiness-scorecard')
const head = await card.boundingBox()
for (const id of ids) {
  const more = p.getByTestId(`gr-more-${id}`)
  if (await more.count()) { await more.click(); await p.waitForTimeout(500) }
}
const rows = ids.map(id => p.getByTestId(`gr-row-${id}`))
await card.evaluate(e => e.scrollIntoView({ block: 'start' })); await p.waitForTimeout(500)
const top = (await card.boundingBox()).y
await p.screenshot({ path: `${out}/${name}-${mode}-header.png`, clip: { x: head.x, y: Math.max(0, top - 4), width: head.width, height: 120 } })
for (let i = 0; i < rows.length; i++) {
  await rows[i].evaluate(e => e.scrollIntoView({ block: 'center' })); await p.waitForTimeout(400)
  const bb = await rows[i].boundingBox()
  await p.screenshot({ path: `${out}/${name}-${mode}-${ids[i]}.png`, clip: { x: bb.x, y: Math.max(0, bb.y - 4), width: bb.width, height: bb.height + 8 } })
}
console.log(name, mode, 'rows', ids.length)
await b.close()
