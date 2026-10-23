// Screenshots for PR #691. Run: OUT=<dir> node shoot.mjs (vite dev server on :5199 serving harness.html).
// data.json is the PR's own backend output (campus service on the test hub network, generic SAGC profile).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'node:fs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = process.env.OUT
const data = JSON.parse(fs.readFileSync(new URL('./data.json', import.meta.url), 'utf8'))
const routes = {
  '/api/campus-electrical/Demo': data.state,
  '/api/campus-electrical/Demo/grid-codes': data.list,
  '/api/campus-electrical/Demo/grid-codes/drafts/sagc_generic': data.draft,
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['guided', 'expert']) {
  const p = await (await browser.newContext({ viewport: { width: 1300, height: 1100 } })).newPage()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    return path in routes ? route.fulfill({ json: routes[path] }) : route.fulfill({ status: 404, json: { detail: 'mock' } })
  })
  // 1: the draft review table with the four new rows
  await p.goto(`${BASE}?view=codes&mode=${mode}`); await p.waitForSelector('[data-testid=draft-sagc_generic]')
  await p.getByRole('button', { name: 'Review draft sagc_generic' }).click()
  await p.waitForSelector('[data-testid="limit-q_capability_pq"]'); await p.waitForTimeout(400)
  const table = p.locator('table:has([data-testid="limit-q_capability_pq"])')
  await table.screenshot({ path: `${OUT}/${mode}-1-draft-table.png` })
  // 2 and 4: the PCC compliance table and the RMS line under it
  await p.goto(`${BASE}?view=results&mode=${mode}`); await p.waitForSelector('[data-testid=compliance-rms]'); await p.waitForTimeout(600)
  const section = p.locator('section, div').filter({ has: p.getByText('PCC compliance', { exact: true }) })
    .filter({ has: p.getByTestId('compliance-rms') }).last()
  await section.scrollIntoViewIfNeeded()
  await section.screenshot({ path: `${OUT}/${mode}-2-compliance.png` })
  const rms = p.getByTestId('compliance-rms')
  const box = await rms.boundingBox(), sbox = await section.boundingBox()
  await p.screenshot({ path: `${OUT}/${mode}-4-rms-line.png`,
    clip: { x: sbox.x, y: box.y - 40, width: sbox.width, height: box.height + 56 } })
  // 3: the hub chapter tile for the grid plan
  await p.goto(`${BASE}?view=chapters&mode=${mode}`); await p.waitForSelector('[data-testid=hub-chapter-grid_plan]'); await p.waitForTimeout(600)
  await p.getByTestId('hub-chapter-grid_plan').screenshot({ path: `${OUT}/${mode}-3-hub-tile.png` })
  console.log(mode, await p.getByTestId('hub-chapter-grid_plan').innerText())
}
await browser.close()
