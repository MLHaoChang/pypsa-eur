import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const [port, out, tag] = process.argv.slice(2)
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['guided', 'expert']) {
  const p = await (await b.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 2 })).newPage()
  await p.route(u => new URL(u).pathname.startsWith('/api/'), r => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }))
  await p.goto(`http://127.0.0.1:${port}/harness.html?mode=${mode}`); await p.waitForTimeout(2500)
  for (const b of await p.getByTestId('gr-more-voltage_unbalance').all()) await b.click(); await p.waitForTimeout(300); await p.getByTestId('score').screenshot({ path: `${out}/${tag}-scorecard-${mode}.png` })
  if (mode === 'guided') { const c = await p.getByTestId('chart').boundingBox(); await p.mouse.move(c.x + 200, c.y + 80); await p.waitForTimeout(500); await p.getByTestId('chart').screenshot({ path: `${out}/${tag}-tooltip.png` }) }
}
await b.close()
