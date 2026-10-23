// Screenshots for PR #707 (g2p 17a harmonics). Run: OUT=<dir> node shoot.mjs (vite dev server on :5199 serving
// harness.html). data.json is the PR's own backend output (dumped through the API test client and
// grid_readiness() on tests/test_grid_readiness.py::_harmonic_site; generic EU RfG/DCC CE profile).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'node:fs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = process.env.OUT
const data = JSON.parse(fs.readFileSync(new URL('./data.json', import.meta.url), 'utf8'))
const gets = {
  '/api/network/links': data.links,
  '/api/network/buses': data.buses,
  '/api/network/carriers': data.carriers,
  '/api/network/asset_schema/Link': data.schema_link,
  '/api/__harness/gr_6pulse': data.gr_6pulse,
  '/api/__harness/gr_12pulse': data.gr_12pulse,
  '/api/__harness/gr_unknown': data.gr_unknown,
}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['guided', 'expert']) {
  const p = await (await browser.newContext({ viewport: { width: 760, height: 1000 }, deviceScaleFactor: 2 })).newPage()
  const unmocked = new Set()
  await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
    const req = route.request(), path = new URL(req.url()).pathname
    // The backend's own refusal of the spectrum (422, exact body from data.json).
    if (req.method() === 'PUT' && path === '/api/network/links/compressor') {
      const body = req.postDataJSON()
      const r = String(body.harmonic_spectrum).includes('51') ? data.refusal_spectrum : data.refusal_source
      return route.fulfill({ status: r.status, json: r.body })
    }
    if (path in gets) return route.fulfill({ json: gets[path] })
    // Side reads the cards make (profiles, provenance, catalog, chat): empty answers, so no error toast shows.
    unmocked.add(`${req.method()} ${path}`)
    const empty = path.includes('/provenance/') ? { fields: {} } : path.includes('/catalog/') ? { attributes: [] }
      : path === '/api/chat/profiles' ? { profiles: [] } : path === '/api/chat/health' ? { ok: false } : {}
    return route.fulfill({ json: empty })
  })
  const editLink = async (name) => {
    await p.goto(`${BASE}?view=link&name=${name}&mode=${mode}`)
    await p.getByTestId('props-edit-link').click()
    await p.waitForSelector('[data-testid=asset-group-performance]'); await p.waitForTimeout(500)
  }
  const panel = p.getByTestId('harness-panel')
  // 1a: a link that is no process unit: no harmonic fields
  await editLink('conveyor')
  await panel.screenshot({ path: `${OUT}/${mode}-1a-link-process-off.png` })
  // 1b: tick Process unit: the four fields appear (empty)
  await p.getByLabel('Process unit', { exact: true }).check(); await p.waitForTimeout(300)
  await panel.screenshot({ path: `${OUT}/${mode}-1b-link-process-on.png` })
  // 1c: the compressor drive with its harmonic inputs filled
  await editLink('compressor')
  await panel.screenshot({ path: `${OUT}/${mode}-1c-link-process-on-filled.png` })
  // 2: a spectrum with order 51 is refused on Save
  const spec = p.getByLabel('Harmonic spectrum', { exact: true })
  await spec.fill('5:20, 51:3')
  await p.getByRole('button', { name: 'Save', exact: true }).click()
  await p.getByText('Harmonic spectrum: order 51 is above 50.', { exact: false }).first().waitFor()
  await spec.evaluate(e => e.scrollIntoView({ block: 'center' })); await p.waitForTimeout(400)
  await p.screenshot({ path: `${OUT}/${mode}-2-refusal-spectrum.png` })
  // 3: the scorecard's harmonic row, its detail open
  for (const c of ['gr_6pulse', 'gr_12pulse', 'gr_unknown']) {
    await p.goto(`${BASE}?view=score&case=${c}&mode=${mode}`)
    const row = p.getByTestId('gr-row-harmonic_distortion'); await row.waitFor()
    await p.getByTestId('gr-more-harmonic_distortion').click()
    await p.getByTestId('gr-detail-harmonic_distortion').waitFor(); await p.waitForTimeout(300)
    await row.scrollIntoViewIfNeeded()
    await row.screenshot({ path: `${OUT}/${mode}-3-scorecard-${c.slice(3)}.png` })
    console.log(mode, c, (await row.innerText()).replace(/\s+/g, ' ').slice(0, 300))
  }
  console.log(mode, 'empty-answered:', [...unmocked].join(', '))
}
await browser.close()
