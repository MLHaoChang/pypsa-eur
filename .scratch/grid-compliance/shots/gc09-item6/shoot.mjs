// GC-09 item 6 shots: the PowerFactory case section in six states, light and dark. The harness page renders the
// real section; this answers its one API read (GET .../powerfactory) with a listing built here.
//   NODE_PATH=$(npm root -g) OUT=<dir> node shoot.mjs
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE ?? 'http://127.0.0.1:5182'
const OUT = process.env.OUT ?? '.'
fs.mkdirSync(OUT, { recursive: true })

const PENDING = 'attribute names pending verification on first PowerFactory import'
const files = n => Array.from({ length: n }, (_, i) => ({ name: `f${i}`, size: 8192 }))
const bundle = (period, hour, over = {}) => ({
  name: `harbour_p${period}_h${hour}`, period, hour, size: 49152, files: files(6),
  not_written: 0, pending_verification: 0, not_written_items: [], pending_items: [], ...over,
})
const shunts = Array.from({ length: 7 }, (_, i) => ({ element: `Shunt ${i + 1}`, attribute: 'p_mw', reason: 'active power not written: no verified attribute' }))
const listing = over => ({
  has_run: true, has_powerfactory: true, stale: false, run_at: '2026-11-24T08:00:00+00:00', skipped: null, error: null,
  bundles: [bundle(2030, 14)], ...over,
})
const STATES = {
  a_gaps: listing({ bundles: [bundle(2030, 14, { not_written: 7, not_written_items: shunts, pending_verification: 1, pending_items: [PENDING] })] }),
  b_clean: listing({ bundles: [bundle(2030, 14), bundle(2030, 4381)] }),
  c_not_run: listing({ has_run: false, has_powerfactory: false, run_at: null, bundles: [] }),
  d_unavailable: null,
  e_stale: listing({ stale: true, bundles: [bundle(2030, 14, { pending_verification: 1, pending_items: [PENDING] })] }),
  f_no_island: listing({ skipped: 'no island critical hours', bundles: [] }),
}

const b = await chromium.launch()
const made = []
try {
  for (const [state, body] of Object.entries(STATES)) {
    for (const theme of ['light', 'dark']) {
      const ctx = await b.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 2, colorScheme: theme })
      const p = await ctx.newPage()
      p.on('pageerror', e => console.error(`${state} ${theme}: ${e.message}`))
      await p.route(/^https?:\/\/[^/]+\/api\//, r => {
        const u = r.request().url()
        if (u.endsWith('/powerfactory')) return body ? r.fulfill({ json: body }) : r.fulfill({ status: 500, json: { detail: 'boom' } })
        if (u.includes('/network/undo/info')) return r.fulfill({ json: { depth: 0, unsaved: false, network_revision: 1 } })
        return r.fulfill({ status: 404, json: {} })
      })
      await p.goto(`${BASE}/gc09pf-harness.html?theme=${theme}`)
      await p.getByText(/Loading the PowerFactory cases/).waitFor({ state: 'detached', timeout: 30000 })
      const got = await p.evaluate(() => document.documentElement.dataset.theme)
      if (got !== theme) throw new Error(`${state}: theme ${got}, wanted ${theme}`)
      await p.waitForTimeout(300)
      const file = path.join(OUT, `${state}-${theme}.png`)
      await p.getByTestId('harness').screenshot({ path: file })
      made.push(file)
      await ctx.close()
    }
  }
} finally {
  await b.close()
}
console.log(`${made.length} shots`)
