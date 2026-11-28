// Shots for SH-07g (report comments in the report viewer). Harness only: needs
// Vite serving pypsa-gui/frontend on PORT (default 5173); copies harness.tsx into
// src/ and removes it in finally. Run: NODE_PATH=$(npm root -g) node shoot.mjs
import { createRequire } from 'node:module'
import { copyFileSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = dirname(fileURLToPath(import.meta.url))
const tmp = resolve(here, '../../../../pypsa-gui/frontend/src/report-comments.tmp.tsx')
const base = `http://127.0.0.1:${process.env.PORT ?? 5173}`
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><script type="module">
import R from "/@react-refresh";R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};
window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true</script></head>
<body class="bg-bg"><div id="root"></div><script type="module" src="/src/report-comments.tmp.tsx"></script></body></html>`

const audit = { unverified: [], verified: [] }
const GRID = { section_id: 'grid', heading: 'Grid connection', source: 'llm', status: 'ok', note: null, audit, blocks: [
  { type: 'paragraph', md: 'The grid connection **binds** in winter evenings, when the heat pumps peak.' },
  { type: 'callout', kind: 'disclosure', text: 'Costs exclude the cost of shed load.' },
  { type: 'bullets', items: ['Line A runs at 94% in January', 'Line B has headroom'] },
  { type: 'table_ref', table_id: 'caps', caption: null },
  { type: 'paragraph', md: 'A 2 MW battery removes most of the evening shortfall.' },
] }
const ASSUMPTIONS = { section_id: 'assumptions', heading: 'Assumptions', source: 'code', status: 'ok', note: null, audit, blocks: [
  { type: 'field', key: 'Weather year', value: '2013' },
  { type: 'field', key: 'Discount rate', value: '7%' },
  { type: 'callout', kind: 'gap', text: 'No measured load profile; the template profile is used.' },
] }
const SUMMARY = { section_id: 'summary', heading: 'Summary', source: 'llm', status: 'ok', note: null, audit, blocks: [
  { type: 'paragraph', md: 'The site meets its reliability goal with a battery and a modest grid upgrade.' },
] }
const doc = v => ({ schema_version: 1, report_id: 'r1', version: v, title: 'Own site study — report', language: 'en',
  created_at: '2026-11-28T09:00:00+00:00', evidence_hash: 'e', profile_id: null, model: null, mode: 'generated',
  template_file_id: null, sections: [SUMMARY, GRID, ASSUMPTIONS],
  tables: { caps: { table_id: 'caps', columns: ['Carrier', 'MW'], rows: [['Grid', '4.0'], ['Battery', '2.0']], caption: 'Capacity by carrier', source_path: null } },
  figures: {} })
const C = (id, over) => ({ comment_id: id, text: 'Say which months.', anchor: { section_id: 'grid', block_index: 0 },
  status: 'open', reason: null, reasons: [], created_at: '2026-11-27T09:00:00+00:00', created_by: 'ana@example.com', ...over })
const LIST = [
  C('o1', { text: 'Name the evening hours this refers to.', created_by: null }),
  C('o2', { text: 'Move the shed-load note next to the totals.', anchor: { section_id: 'grid', block_index: 1 }, reason: 'outside_edit_set', reasons: ['outside_edit_set'] }),
  C('a1', { text: 'Round the totals to one decimal.', anchor: { section_id: 'grid', table_id: 'caps' }, status: 'addressed', revision: { version: 3, digest: 'x' } }),
  C('w1', { text: 'Drop Line B.', anchor: { section_id: 'grid', block_index: 2 }, status: 'withdrawn' }),
  C('w2', { text: 'Too long.', anchor: { section_id: 'grid', block_index: 4 }, status: 'withdrawn', created_by: 'ben@example.com' }),
  C('w3', { text: 'Add the battery cost.', anchor: { section_id: 'grid', block_index: 4 }, status: 'withdrawn', created_by: 'ben@example.com' }),
  C('w4', { text: 'Use MW, not kW.', anchor: { section_id: 'grid', table_id: 'caps' }, status: 'withdrawn' }),
]

const browser = await chromium.launch()
async function open(name, { comments = [], stale = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 1 })
  page.on('pageerror', e => console.log(name, 'pageerror', e.message))
  await page.route('**/report-comments.html*', r => r.fulfill({ contentType: 'text/html', body: PAGE }))
  await page.route(`${base}/api/**`, r => {
    const req = r.request(); const u = new URL(req.url()); const p = u.pathname
    if (p.endsWith('/generate/status')) return r.fulfill({ status: 204, body: '' })
    if (p.endsWith('/capabilities')) return r.fulfill({ json: { pdf: false } })
    if (p.endsWith('/uploads')) return r.fulfill({ json: [] })
    if (p.endsWith('/r1/template')) return r.fulfill({ json: { template_file_id: null, mode: null, language: null, outline: null, plan: null } })
    if (p.endsWith('/r1/comments') && req.method() === 'POST') {
      if (stale) return r.fulfill({ status: 409, json: { detail: { error_kind: 'revision_stale_base', message: 'x' } } })
      return r.fulfill({ json: C('new', {}) })
    }
    if (p.endsWith('/r1/comments')) return r.fulfill({ json: { report_id: 'r1', latest_version: 3, digest: 'd'.repeat(64), comments } })
    if (p.endsWith('/reports/r1')) return r.fulfill({ json: doc(Number(u.searchParams.get('version') ?? 3)) })
    console.log(name, 'unmocked', req.method(), p)
    return r.fulfill({ status: 404, json: { detail: 'not mocked' } })
  })
  await page.goto(`${base}/report-comments.html`)
  await page.waitForSelector('[data-testid=report-viewer] [data-testid=section-comment]', { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(400)
  return page
}
const shot = async (page, sel, name) => {
  await page.waitForTimeout(250)
  await page.locator(sel).first().screenshot({ path: resolve(here, `${name}.png`) })
}
// The Grid connection card: walk up from its Regenerate… button to the element
// just below the one that holds another section's Regenerate… button.
const shotGrid = async (page, name) => {
  await page.waitForTimeout(250)
  const box = await page.evaluate(() => {
    const regen = [...document.querySelectorAll('[data-testid=report-viewer] button')].filter(b => b.textContent.includes('Regenerate'))
    let el = regen[1]
    while (el.parentElement && el.parentElement.querySelectorAll('button').length
      && [...el.parentElement.querySelectorAll('button')].filter(b => b.textContent.includes('Regenerate')).length < 2) el = el.parentElement
    const r = el.getBoundingClientRect()
    return { x: r.x - 8, y: r.y + window.scrollY - 8, width: r.width + 16, height: r.height + 16 }
  })
  await page.screenshot({ path: resolve(here, `${name}.png`), clip: box, fullPage: true })
}

copyFileSync(resolve(here, 'harness.tsx'), tmp)
try {
  // 1. form open
  let page = await open('01')
  await page.locator('[data-testid=section-comment]').nth(1).click()
  await page.selectOption('[data-testid=comment-anchor]', 'tcaps')
  await page.fill('[data-testid=comment-text]', 'Round the totals to one decimal and say which year they are for.')
  await shotGrid(page, '01-form-open'); await page.close()

  // 2. list + fold, 3. withdraw confirm
  page = await open('02', { comments: LIST })
  await shotGrid(page, '02-list-fold')
  await page.click('[data-testid=comment-withdraw]')
  await shotGrid(page, '03-withdraw-confirm'); await page.close()

  // 4. stale refusal, 5. after Reload
  page = await open('04', { stale: true })
  await page.locator('[data-testid=section-comment]').nth(1).click()
  await page.selectOption('[data-testid=comment-anchor]', 'b2')
  await page.fill('[data-testid=comment-text]', 'Say how often Line A goes over 90%.')
  await page.click('[data-testid=comment-submit]')
  await page.waitForSelector('[data-testid=comment-refusal]')
  await shotGrid(page, '04-stale-refusal')
  await page.click('[data-testid=comment-reload]')
  await page.waitForTimeout(800)
  await shotGrid(page, '05-stale-restored'); await page.close()

  // 6. old version
  page = await open('06', { comments: LIST.slice(0, 3) })
  await page.locator('[data-testid=report-viewer] select').first().selectOption('2')
  await page.waitForSelector('[data-testid=comments-latest-only]')
  await shot(page, '[data-testid=shot-main]', '06-old-version'); await page.close()

  // 7. no commentable part beside one with paragraphs
  page = await open('07')
  await shot(page, '[data-testid=shot-main]', '07-no-commentable'); await page.close()
} finally {
  await browser.close()
  rmSync(tmp, { force: true })
}
