// Screenshots for PR #689. Run: OUT=<dir> node shoot.mjs (vite dev server on :5199 serving harness.html).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const BASE = 'http://127.0.0.1:5199/harness.html'
const OUT = process.env.OUT
const DOC = { id: 'a'.repeat(64), sha256: 'a'.repeat(64), filename: 'SAGC-May-2026.pdf', size: 3250000,
  uploaded_at: '2026-10-23T08:00:00+00:00', pages: 292, page_ranges: '1-40,120-180' }
const list = docs => ({ shipped: {}, published: [], drafts: [], documents: docs, extraction_available: false })
const LONG = 'the PDF has 292 pages; the extraction reads at most 100. Select the pages with the connection requirements '
  + 'as a page range, such as 1-40,120-180 (at most 100 pages in all)'
const BAD = "the page selection '40-1' runs backwards; write it as 1-40"
const pdf = { name: 'SAGC-May-2026.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7 sample') }
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
for (const mode of ['expert', 'guided']) {
  const shot = async (name, docs, upload, act) => {
    const p = await (await browser.newContext({ viewport: { width: 1000, height: 700 } })).newPage()
    await p.route(url => new URL(url).pathname.startsWith('/api/'), route => {
      const r = route.request(), path = new URL(r.url()).pathname
      if (r.method() === 'POST' && path.endsWith('/grid-codes/documents')) return route.fulfill(upload)
      if (path.endsWith('/grid-codes')) return route.fulfill({ json: list(docs) })
      return route.fulfill({ status: 404, json: { detail: 'mock' } })
    })
    await p.goto(`${BASE}?mode=${mode}`); await p.waitForSelector('[data-testid=grid-codes]'); await p.waitForTimeout(400)
    if (act) await act(p)
    await p.waitForTimeout(400)
    await p.getByTestId('grid-codes').screenshot({ path: `${OUT}/${name}-${mode}.png` })
  }
  await shot('01-empty-upload-row', [], null)
  await shot('02-long-pdf-refused', [], { status: 422, json: { detail: LONG } },
    p => p.getByLabel('Grid-code document').setInputFiles(pdf))
  await shot('03-malformed-range', [], { status: 422, json: { detail: BAD } }, async p => {
    await p.getByLabel('Pages (optional)').fill('40-1'); await p.getByLabel('Grid-code document').setInputFiles(pdf)
  })
  await shot('04-row-with-selection', [DOC], null)
}
await browser.close()
