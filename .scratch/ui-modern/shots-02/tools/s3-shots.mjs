import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
const [project, outDir, state, ...steps] = process.argv.slice(2)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: Number(process.env.VH || 900) }, storageState: state })
const p = await ctx.newPage()
p.setDefaultTimeout(20000)
const log = (...a) => console.log(...a)
// GAP=1 drops five price rows (hatched cells); ROWS=744 keeps the first 744 price rows (a 31-day window).
if (process.env.GAP || process.env.ROWS) {
  await p.route('**/api/results/prices**', async route => {
    const r = await route.fetch(); const j = await r.json()
    const drop = i => (process.env.ROWS ? i >= Number(process.env.ROWS) : i >= 30 && i <= 34)
    for (const k of ['index', 'data', 'data_adjusted', 'periods']) if (Array.isArray(j[k])) j[k] = j[k].filter((_, i) => !drop(i))
    await route.fulfill({ response: r, json: j })
  })
}
// EMPTY_FLOWS=1 empties the energy-flow rows (the "No energy moved" state).
if (process.env.EMPTY_FLOWS) {
  await p.route('**/api/results/energy_flows**', async route => {
    const r = await route.fetch(); const j = await r.json(); j.rows = []
    await route.fulfill({ response: r, json: j })
  })
}
await p.goto((process.env.BASE_URL || 'http://127.0.0.1:5173') + '/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click({ timeout: 8000 }); await p.waitForTimeout(4500)
// GUIDED=1 stays in (or switches to) Guided.
await p.getByRole('button', { name: process.env.GUIDED ? 'Guided' : 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
if (!process.env.GUIDED) { await p.getByRole('button', { name: 'Results', exact: true }).first().click({ timeout: 8000 }); await p.waitForTimeout(3500) }
const frame = title => p.locator('[data-chart-frame]').filter({ has: p.getByRole('heading', { name: title }) }).first()
let theme = 'dark'
for (const s of steps) {
  const [op, arg, file] = s.split('::')
  try {
    if (op === 'click') { await p.getByRole('button', { name: arg }).first().click({ timeout: 6000 }); await p.waitForTimeout(2500) }
    else if (op === 'theme') { await p.locator('button[title^="Switch to"][title$="theme"]').first().evaluate(el => el.click()); await p.waitForTimeout(900); theme = theme === 'dark' ? 'light' : 'dark' }
    else if (op === 'shot') { const f = frame(arg); await f.evaluate((el, b) => el.scrollIntoView({ block: b }), process.env.BLOCK || 'center'); await p.waitForTimeout(2500); await p.waitForTimeout(900); await f.screenshot({ path: `${outDir}/${file.replace('THEME', theme)}` }); log('shot', file.replace('THEME', theme)) }
    else if (op === 'hover') { const f = frame(arg); await f.evaluate((el, b) => el.scrollIntoView({ block: b }), process.env.BLOCK || 'center'); await p.waitForTimeout(2500); const svg = f.locator('svg[role="img"]'); const bx = await svg.boundingBox(); await p.mouse.move(bx.x + bx.width * 0.55, bx.y + bx.height * 0.6); await p.waitForTimeout(500); await f.screenshot({ path: `${outDir}/${file}` }); log('shot', file, await f.locator('[data-heat-readout]').innerText()) }
    else if (op === 'view') { const f = frame(arg); await f.evaluate((el, b) => el.scrollIntoView({ block: b }), process.env.BLOCK || 'center'); await p.waitForTimeout(2500); await p.waitForTimeout(900); await p.screenshot({ path: `${outDir}/${file.replace('THEME', theme)}` }); log('view', file) }
    else if (op === 'window') { await p.getByRole('button', { name: /Horizon filter/ }).first().click(); await p.waitForTimeout(600); const inp = p.locator('input[type="datetime-local"]'); await inp.nth(0).fill(arg); await inp.nth(1).fill(file); await p.waitForTimeout(2500); log('window', arg, file) }
    else if (op === 'heads') { log('heads', JSON.stringify((await p.locator('h2, h3, header span.font-semibold').allInnerTexts()).filter(t => /storage/i.test(t))).slice(0, 500)) }
    else if (op === 'tid') { log('tid', arg, (await p.getByTestId(arg).first().innerText()).replace(/\s+/g, ' ').slice(0, Number(process.env.TEXT || 300))) }
    else if (op === 'buttons') { log('buttons', JSON.stringify((await p.getByRole('button').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()).filter(t => t && t.length < 40)).slice(0, 1500)) }
    else if (op === 'text') { const f = frame(arg); log('visible', await f.isVisible(), (await f.innerText()).replace(/\s+/g, ' ').slice(0, Number(process.env.TEXT || 300))) }
    else if (op === 'count') { log('count', arg, await p.locator('[data-chart-frame]').filter({ has: p.getByRole('heading', { name: arg }) }).count(), JSON.stringify(await p.locator('[data-chart-frame] h3').allInnerTexts()).slice(0, 700)) }
  } catch (e) { log('FAIL', s, e.message.slice(0, 140).replace(/\n/g, ' ')) }
}
await b.close()
