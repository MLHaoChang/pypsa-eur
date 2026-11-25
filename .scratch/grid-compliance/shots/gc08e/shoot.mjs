// GC-08e shots: the ssti_uif scorecard row (S1 S2 S5 S6) and the pack's ssti_uif table (P1 P2), Guided and
// Expert, light and dark. The harness page (gc08e-harness.tsx) renders the real components; this answers every
// /api request from data.json (make_data.py: the backend's own grid_readiness and pack output).
//   NODE_PATH=$(npm root -g) DATA=data.json OUT=<dir> node shoot.mjs
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE ?? 'http://127.0.0.1:5181'
const OUT = process.env.OUT ?? '.'
const data = JSON.parse(fs.readFileSync(process.env.DATA, 'utf8'))
fs.mkdirSync(OUT, { recursive: true })

const doc = state => ({
  schema_version: 1, report_id: `gc08e-${state}`, version: 1, title: 'Grid compliance pack', language: 'en',
  created_at: '2026-11-24T00:00:00Z', evidence_hash: 'x', mode: 'evidence_only',
  sections: [{
    section_id: 'ssti', heading: 'SSTI screening', source: 'code', status: 'ok',
    blocks: [{ type: 'table_ref', table_id: 'ssti_uif' }], audit: { unverified: [], verified: [] },
  }],
  tables: { ssti_uif: data.pack[state] }, figures: {},
})

const b = await chromium.launch()
const made = []
try {
  for (const state of [...Object.keys(data.scorecard), ...Object.keys(data.pack)]) {
    for (const mode of ['guided', 'expert']) {
      for (const theme of ['light', 'dark']) {
        const ctx = await b.newContext({ viewport: { width: 1000, height: 1400 }, deviceScaleFactor: 2, colorScheme: theme })
        const p = await ctx.newPage()
        p.on('pageerror', e => console.error(`${state} ${mode} ${theme}: ${e.message}`))
        await p.route(/^https?:\/\/[^/]+\/api\//, r => {
          const u = r.request().url()
          if (u.includes('/grid-readiness')) return r.fulfill({ json: data.scorecard[state] })
          if (u.includes('/network/undo/info')) return r.fulfill({ json: { depth: 0, unsaved: false, network_revision: 1 } })
          if (/\/reports\/gc08e-/.test(u)) return r.fulfill({ json: doc(state) })
          return r.fulfill({ status: 404, json: {} })
        })
        await p.goto(`${BASE}/gc08e-harness.html?state=${state}&mode=${mode}&theme=${theme}`)
        let el
        if (state.startsWith('P')) {
          el = p.getByTestId('block-table')
          await el.waitFor({ timeout: 30000 })
        } else {
          el = p.getByTestId('gr-row-ssti_uif')
          await el.waitFor({ timeout: 30000 })
          await p.getByTestId('gr-more-ssti_uif').click()
          await p.getByTestId('gr-detail-ssti_uif').waitFor()
        }
        const got = await p.evaluate(() => document.documentElement.dataset.theme)
        if (got !== theme) throw new Error(`${state}: theme ${got}, wanted ${theme}`)
        await p.waitForTimeout(300)
        const file = path.join(OUT, `${state}-${mode}-${theme}.png`)
        await el.screenshot({ path: file })
        made.push(file)
        await ctx.close()
      }
    }
  }
} finally {
  await b.close()
}
console.log(`${made.length} shots`)
