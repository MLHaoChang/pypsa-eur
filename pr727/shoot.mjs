// Screenshots for PR #727 (g2p 17a flicker). Run: OUT=<dir> node shoot.mjs (vite dev server on :5199 serving
// harness.html). data.json is the PR's own backend output (dump_test.py: the API test client on the
// test_process_harmonic_fields network, grid_readiness() on tests/test_grid_readiness.py::_flicker_site with the
// generic EU RfG/DCC CE profile, and a grid-code draft holding that profile).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'node:fs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = process.env.OUT
const data = JSON.parse(fs.readFileSync(new URL('./data.json', import.meta.url), 'utf8'))
const CASES = ['gr_pass', 'gr_warn', 'gr_nostep', 'gr_nosk']
const gets = {
  '/api/network/links': data.links,
  '/api/network/buses': data.buses,
  '/api/network/carriers': data.carriers,
  '/api/network/asset_schema/Link': data.schema_link,
  '/api/campus-electrical/Demo/grid-codes': data.grid_codes,
  '/api/campus-electrical/Demo/grid-codes/drafts/eu_flicker': data.draft,
  ...Object.fromEntries(CASES.map(c => [`/api/__harness/${c}`, data[c]])),
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['guided', 'expert']) {
  const p = await (await browser.newContext({ viewport: { width: 760, height: 1000 }, deviceScaleFactor: 2 })).newPage()
  const unmocked = new Set()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const req = route.request(), path = new URL(req.url()).pathname
    if (path in gets) return route.fulfill({ json: gets[path] })
    // Side reads the cards make (profiles, provenance, catalog, chat): empty answers, so no error toast shows.
    unmocked.add(`${req.method()} ${path}`)
    const empty = path.includes('/provenance/') ? { fields: {} } : path.includes('/catalog/') ? { attributes: [] }
      : path === '/api/chat/profiles' ? { profiles: [] } : path === '/api/chat/health' ? { ok: false } : {}
    return route.fulfill({ json: empty })
  })
  const panel = p.getByTestId('harness-panel')
  const openEdit = async () => {
    await p.getByTestId('props-edit-link').click()
    await p.waitForSelector('[data-testid=asset-group-performance]'); await p.waitForTimeout(500)
  }
  const showField = async (label) => {
    await p.getByLabel(label, { exact: true }).evaluate(e => e.scrollIntoView({ block: 'center' }))
    await p.waitForTimeout(300)
  }
  // 1a: a link that is no process unit: no flicker fields
  await p.goto(`${BASE}?view=link&name=conveyor&mode=${mode}`); await openEdit()
  await panel.screenshot({ path: `${OUT}/${mode}-1a-link-process-off.png` })
  // 1b: tick Process unit: the two flicker fields appear (empty)
  await p.getByLabel('Process unit', { exact: true }).check(); await p.waitForTimeout(300)
  await panel.screenshot({ path: `${OUT}/${mode}-1b-link-process-on.png` })
  // 1c: the heater with its flicker inputs filled
  await p.goto(`${BASE}?view=link&name=heater&mode=${mode}`); await openEdit()
  await panel.screenshot({ path: `${OUT}/${mode}-1c-link-process-on-filled.png` })
  // 2: the grid-code draft's two flicker rows
  await p.setViewportSize({ width: 1280, height: 1000 })
  await p.goto(`${BASE}?view=codes&mode=${mode}`)
  await p.getByRole('button', { name: 'Review draft eu_flicker' }).click()
  const pst = p.getByTestId('limit-pst_limit'); await pst.waitFor()
  await pst.scrollIntoViewIfNeeded(); await p.waitForTimeout(300)
  const box = await pst.boundingBox(), plt = await p.getByTestId('limit-plt_limit').boundingBox()
  await p.screenshot({ path: `${OUT}/${mode}-2-grid-code-rows.png`,
    clip: { x: 0, y: Math.max(0, box.y - 40), width: 1280, height: plt.y + plt.height - box.y + 80 } })
  console.log(mode, 'codes', (await pst.innerText()).replace(/\s+/g, ' ').slice(0, 200))
  await p.setViewportSize({ width: 760, height: 1000 })
  // 3: the scorecard's flicker row, its detail open
  for (const c of CASES) {
    await p.goto(`${BASE}?view=score&case=${c}&mode=${mode}`)
    const row = p.getByTestId('gr-row-flicker'); await row.waitFor()
    await p.getByTestId('gr-more-flicker').click()
    await p.getByTestId('gr-detail-flicker').waitFor(); await p.waitForTimeout(300)
    await row.scrollIntoViewIfNeeded()
    await row.screenshot({ path: `${OUT}/${mode}-3-scorecard-${c.slice(3)}.png` })
    console.log(mode, c, (await row.innerText()).replace(/\s+/g, ' ').slice(0, 300))
  }
  // 4 (Expert): "Go to field" on the no-step row lands on the compressor's card, its step field empty
  if (mode === 'expert') {
    await p.goto(`${BASE}?view=score&case=gr_nostep&mode=${mode}`)
    await p.getByTestId('gr-jump-flicker').click()
    await panel.waitFor(); await openEdit(); await showField('Largest power step (MVA)')
    await panel.screenshot({ path: `${OUT}/${mode}-4-go-to-field-landing.png` })
  }
  console.log(mode, 'empty-answered:', [...unmocked].join(', '))
}
await browser.close()
