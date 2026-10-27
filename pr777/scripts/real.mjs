import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const [out, tag] = process.argv.slice(2)
fs.mkdirSync(out, { recursive: true })
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const ctx = await b.newContext({ viewport: { width: 1800, height: 1400 } })
await ctx.addInitScript(() => {
  localStorage.setItem('network-diagram:ui-mode', 'expert'); localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  localStorage.setItem('network-diagram:current-project', 'dcsurf-demo')
})
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:' + (process.env.PORT||5173) + '/'); await p.waitForTimeout(6000)
if (await p.locator('input[type=password]').count()) {
  await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com')
  await p.locator('input[type=password]').fill(pw); await p.keyboard.press('Enter'); await p.waitForTimeout(3000)
}
await p.getByText('Resume project').first().click(); await p.waitForTimeout(5000)
await p.getByText('Grid connection', { exact: true }).first().click(); await p.waitForTimeout(4000)
await p.screenshot({ path: `${out}/${tag}-gridconn.png` })
const f = p.locator('input[type=file]').first()
console.log('file inputs', await p.locator('input[type=file]').count())
if (await f.count()) {
  await f.setInputFiles({ name: 'grid-code.pdf', mimeType: 'application/pdf', buffer: Buffer.from('not a pdf at all') })
  await p.waitForTimeout(3000)
  await p.screenshot({ path: `${out}/${tag}-refusal-full.png` })
  const err = p.locator('p.text-danger').first()
  console.log('refusal:', await err.innerText().catch(() => '(none)'))
  if (await err.count()) await err.screenshot({ path: `${out}/${tag}-a-refusal.png` })
}
await b.close()
