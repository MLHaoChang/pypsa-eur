// S6 before/after shots of the Results tabs (full viewport, 1440 wide, 2x, dark AND light per shot).
//
//   node s6-shots.mjs <project> <state.json> steps…
//
// Env: BASE_URL (default http://127.0.0.1:5173), PREFIX (default s6-before), OUT (default ../s6),
//      VH (viewport height, default 900), THEMES (default dark,light), MAXPAGES (default 6).
//      MOCK_RESERVES=1 answers /api/results/reserves with a made-up two-product payload (no template
//      carries reserve products), so Storage cycling shows the Reserve commitments section.
// Files: <OUT>/<PREFIX>-<slug>-<n>-<dark|light>.png; n counts per slug.
// Steps (split on "::"):
//   g::<id>            click the group  [data-testid=results-group-<id>]
//   ct::<id>           click the card tab [data-testid=results-cardtab-<id>]
//   click::<name>      click the first button with that exact name (falls back to a substring match)
//   tid::<testid>      click a test id
//   info               print group / card-tab / sub-view buttons, the headings and the scroller size
//   pages::<slug>      shoot the whole tab body, one viewport per shot, from the top
//   at::<slug>::<text> scroll the first heading/label containing <text> to the top, then shoot
//   wait::<ms>
//   vh::<px>           resize the viewport height (Load shape has no scroller of its own: shoot it tall)
import { createRequire } from 'module'
import path from 'path'
import { fileURLToPath } from 'url'
const { chromium } = createRequire(import.meta.url)('playwright')
const [project, state, ...steps] = process.argv.slice(2)
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:5173').replace(/\/$/, '')
const PREFIX = process.env.PREFIX || 's6-before'
const OUT = process.env.OUT || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 's6')
const THEMES = (process.env.THEMES || 'dark,light').split(',')
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: Number(process.env.VH || 900) }, deviceScaleFactor: 2, storageState: state })
const p = await ctx.newPage()
p.setDefaultTimeout(20000)
const log = (...a) => console.log(...a)
if (process.env.MOCK_RESERVES) {
  const N = 48, snapshots = Array.from({ length: N }, (_, i) => new Date(Date.UTC(2030, 0, 1, i)).toISOString().slice(0, 19))
  const wave = (a, b, f) => snapshots.map((_, i) => Math.round((a + b * Math.sin(i / f)) * 10) / 10)
  const line = (id, amount) => ({ period: null, contract_id: `reserve:${id}`, payer: 'tso', payee: 'site', value_stream: 'reserve_capacity', quantity_mwh: null, amount, flags: ['activation_not_valued'] })
  const json = {
    products: [
      { id: 'afrr_up', kind: 'secondary', direction: 'up', sustain_h: 1, counterparty: 'tso', price_source: 'demo' },
      { id: 'afrr_down', kind: 'secondary', direction: 'down', sustain_h: 1, counterparty: 'tso', price_source: 'user' },
    ],
    snapshots,
    storages: [{ name: 'bess_1', string: true, floor_mwh: 4, e_cap_mwh: 10, soc_mwh: wave(6.5, 1.5, 4), committed: { afrr_up: wave(2, 1, 3), afrr_down: wave(2, 0.8, 5) } }],
    lines: [line('afrr_up', 1200), line('afrr_down', 800)],
    flags: [], headroom_h_per_string: 1, currency: 'EUR', money_year: 2030,
    not_modelled: 'activation energy and its price, cycling wear and non-delivery penalties are not modelled: capacity payments only',
  }
  await p.route('**/api/results/reserves**', route => route.fulfill({ json }))
}
await p.goto(BASE + '/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click({ timeout: 8000 }); await p.waitForTimeout(4500)
await p.getByRole('button', { name: 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
await p.getByRole('button', { name: 'Results', exact: true }).first().click({ timeout: 8000 }); await p.waitForTimeout(3500)

const theme = () => p.evaluate(() => document.documentElement.getAttribute('data-theme') || 'dark')
const setTheme = async t => {
  if (await theme() === t) return
  await p.locator('button[title^="Switch to"][title$="theme"]').first().evaluate(el => el.click()); await p.waitForTimeout(1200)
}
// The tab body's scroller: the scrollable element with the most hidden height, right of the left rail.
const markScroller = () => p.evaluate(() => {
  document.querySelectorAll('[data-s6-scroller]').forEach(e => e.removeAttribute('data-s6-scroller'))
  let best = null, gap = 0
  for (const el of document.querySelectorAll('main *, #root *')) {
    const oy = getComputedStyle(el).overflowY
    if (oy !== 'auto' && oy !== 'scroll') continue
    const r = el.getBoundingClientRect()
    if (r.width < 600) continue
    const g = el.scrollHeight - el.clientHeight
    if (g > 40 && g > gap) { gap = g; best = el }
  }
  if (!best) return null
  best.setAttribute('data-s6-scroller', '')
  return { scrollHeight: best.scrollHeight, clientHeight: best.clientHeight, top: best.scrollTop }
})
const counters = {}
const visibleHeads = () => p.evaluate(() => [...document.querySelectorAll('h2, h3, h4')].filter(h => {
  const r = h.getBoundingClientRect(); return r.width > 0 && r.top >= 0 && r.bottom <= innerHeight
}).map(h => h.textContent.replace(/\s+/g, ' ').trim().slice(0, 60)).filter(Boolean))
const shoot = async slug => {
  const n = counters[slug] = (counters[slug] || 0) + 1
  const start = await theme()
  for (const t of THEMES) {
    await setTheme(t); await p.waitForTimeout(700)
    const f = `${PREFIX}-${slug}-${n}-${t}.png`
    await p.screenshot({ path: path.join(OUT, f) })
    log('shot', f, t === THEMES[0] ? JSON.stringify(await visibleHeads()).slice(0, 600) : '')
  }
  await setTheme(start)
}
for (const s of steps) {
  const [op, arg, arg2] = s.split('::')
  try {
    if (op === 'g') { await p.getByTestId(`results-group-${arg}`).first().click({ timeout: 6000 }); await p.waitForTimeout(2500) }
    else if (op === 'ct') { await p.getByTestId(`results-cardtab-${arg}`).first().click({ timeout: 6000 }); await p.waitForTimeout(2500) }
    else if (op === 'tid') { await p.getByTestId(arg).first().click({ timeout: 6000 }); await p.waitForTimeout(2500) }
    else if (op === 'wait') { await p.waitForTimeout(Number(arg)) }
    else if (op === 'vh') { await p.setViewportSize({ width: 1440, height: Number(arg) }); await p.waitForTimeout(1500) }
    else if (op === 'click') {
      const exact = p.getByRole('button', { name: arg, exact: true })
      const l = (await exact.count()) ? exact : p.getByRole('button', { name: arg })
      await l.first().click({ timeout: 6000 }); await p.waitForTimeout(2500)
    }
    else if (op === 'info') {
      const txt = async sel => JSON.stringify((await p.locator(sel).allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()).filter(Boolean)).slice(0, 900)
      log('groups', await txt('[data-testid^="results-group-"]'))
      log('cardtabs', await txt('[data-testid^="results-cardtab-"]'))
      log('subviews', await txt('[data-testid="results-subviews"] button'))
      log('scroller', JSON.stringify(await markScroller()))
      log('heads', await p.evaluate(() => JSON.stringify([...document.querySelectorAll('h2, h3, h4')].filter(h => h.getBoundingClientRect().width > 0).map(h => h.textContent.replace(/\s+/g, ' ').trim().slice(0, 50))).slice(0, 2500)))
      log('svg', await p.locator('svg[role="img"], .recharts-wrapper, canvas').count(), 'frames', await p.locator('[data-chart-frame]').count())
    }
    else if (op === 'pages') {
      const sc = await markScroller()
      if (!sc) { await shoot(arg); continue }
      const step = Math.floor(sc.clientHeight * 0.88)
      const max = Number(process.env.MAXPAGES || 6)
      for (let i = 0, y = 0; i < max; i++, y += step) {
        const top = await p.evaluate(y => { const e = document.querySelector('[data-s6-scroller]'); e.scrollTop = y; return e.scrollTop }, y)
        await p.waitForTimeout(1500)
        await shoot(arg)
        if (top + sc.clientHeight >= sc.scrollHeight - 2) break
      }
    }
    else if (op === 'at') {
      const ok = await p.evaluate(text => {
        const els = [...document.querySelectorAll('h2, h3, h4, header span, [data-chart-frame] span, [class*="font-semibold"]')]
        const el = els.find(e => e.getBoundingClientRect().width > 0 && e.textContent.replace(/\s+/g, ' ').includes(text))
        if (!el) return false
        ;(el.closest('[data-chart-frame]') || el).scrollIntoView({ block: 'start' }); return true
      }, arg2)
      if (!ok) { log('FAIL', s, 'no visible heading with that text'); continue }
      await p.waitForTimeout(2000); await shoot(arg)
    }
  } catch (e) { log('FAIL', s, e.message.slice(0, 160).replace(/\n/g, ' ')) }
}
await b.close()
