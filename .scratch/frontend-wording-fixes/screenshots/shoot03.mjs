import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const OUT = new URL('.', import.meta.url).pathname
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['expert', 'guided']) {
  const p = await b.newPage({ viewport: { width: 1100, height: 700 } })
  await p.goto(`http://127.0.0.1:5199/harness.html?view=edit&mode=${mode}`); await p.waitForTimeout(1000)
  await p.screenshot({ path: `${OUT}03-edit-card-rounded-${mode}.png` })
}
await b.close()
