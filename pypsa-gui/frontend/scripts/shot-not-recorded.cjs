// Dev-only: UX-review screenshots for the "— not recorded" change (assurance
// issue 02, A1.6/A1.7-b). Mocks only the lost_load and emissions result routes;
// everything else is the real API (:8000) and Vite (:5173) on a solved
// multi-period template project.
//
// Usage: node scripts/shot-not-recorded.cjs [out-dir]   (default /tmp/shots-not-recorded)
const { chromium } = require('playwright')
const OUT = process.argv[2] || '/tmp/shots-not-recorded'
require('node:fs').mkdirSync(OUT, { recursive: true })
const WEB = 'http://127.0.0.1:5173'
const API = 'http://127.0.0.1:8000'
const PROJECT = 'Data Center Energy Hub (staged growth)'

async function realLostLoad() { return (await fetch(`${API}/api/results/lost_load`)).json() }
async function realEmissions() { return (await fetch(`${API}/api/results/emissions`)).json() }

function unknownLostLoad(real) {
  // Shed 2–6 MW on it_load for a few evening hours in each period; the solve
  // recorded neither totals nor VOLL.
  const data = real.data.map((row, i) => row.map((v, j) => (j === 0 && i % 24 >= 18 && i % 24 <= 21 && i % 72 < 24 ? 2 + (i % 5) : 0)))
  return { ...real, data, total_mwh: null, total_cost_eur: null, voll_eur_per_mwh: null }
}

function capsNoShadow(real) {
  const used = real.total_tCO2
  const caps = [
    { name: 'CO2Limit', investment_period: null, scope: 'horizon', cap_tCO2: Math.round(used * 1.0),
      used_tCO2: used, shadow_price_eur_per_tCO2: null, slack_tCO2: 0, binding: true },
    { name: 'CO2Limit-2035', investment_period: 2035, scope: 'period', cap_tCO2: Math.round(used * 0.5),
      used_tCO2: used * 0.45, shadow_price_eur_per_tCO2: 85.4, slack_tCO2: used * 0.05, binding: false },
  ]
  return { ...real, cap: { active: true, name: 'CO2Limit', cap_tCO2: caps[0].cap_tCO2,
    shadow_price_eur_per_tCO2: null, slack_tCO2: 0 }, caps }
}

async function newPage(browser, mode, { lostLoad, emissions }) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1600 } })
  await ctx.addInitScript((m) => {
    try {
      if (sessionStorage.getItem('shot:seeded')) return
      localStorage.setItem('network-diagram:ui-mode', 'expert')
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.removeItem('results:active-tab')
      sessionStorage.setItem('shot:seeded', '1')
    } catch {}
  }, mode)
  const page = await ctx.newPage()
  page.on('pageerror', e => console.log(`[${mode}] pageerror`, e.message))
  if (lostLoad) await page.route(/\/api\/results\/lost_load(\?|$)/, r => r.fulfill({ json: lostLoad }))
  if (emissions) await page.route(/\/api\/results\/emissions(\?|$)/, r => r.fulfill({ json: emissions }))
  await page.goto(`${WEB}/app?project=${encodeURIComponent(PROJECT)}`)
  await page.getByRole('button', { name: 'Results', exact: true }).first().waitFor({ timeout: 60_000 })
  // Guided hides the sidebar Results entry: open Results in Expert, then
  // switch; the mode switch keeps Results open.
  await page.getByRole('button', { name: 'Results', exact: true }).first().click()
  if (mode === 'guided') await page.locator('[data-testid="ui-mode-guided"]').click()
  await page.locator('[data-testid="results-summary"]').waitFor({ timeout: 60_000 })
  await page.waitForTimeout(1500)
  return { ctx, page }
}

// The app's own explicit tab request (what the assistant and the in-app
// links call); in Guided it shows an expert tab as "Advanced".
async function openTab(page, tab) {
  await page.evaluate(async (t) => {
    const m = await import('/src/store/uiStore.ts')
    m.useUIStore.getState().requestResultsTab(t)
  }, tab)
  await page.waitForTimeout(2500)
}

async function soft(page, name, p) {
  try { await p } catch (e) { console.log('WAIT FAILED', name, e.message.split('\n')[0]); await page.screenshot({ path: `${OUT}/_fail-${name}.png` }) }
}

async function shot(page, name, locator) {
  const file = `${OUT}/${name}.png`
  if (locator) await locator.scrollIntoViewIfNeeded()
  await page.screenshot({ path: file })
  console.log('saved', file)
}

;(async () => {
  const browser = await chromium.launch()
  const real = await realLostLoad()
  const realEm = await realEmissions()
  const tabs = async page => page.evaluate(() =>
    [...document.querySelectorAll('[data-testid^="results-tab-"]')].map(e => e.getAttribute('data-testid')))
  for (const mode of (process.env.MODES || 'guided,expert').split(',')) {
    // (a) LostLoad with unknown VOLL/totals over a shed series
    {
      const { ctx, page } = await newPage(browser, mode, { lostLoad: unknownLostLoad(real) })
      await openTab(page, 'lostload')
      await soft(page, `${mode}-a`, page.getByText('—: not recorded by this solve').first().waitFor({ timeout: 20_000 }))
      if (process.env.DBG) console.log(await page.evaluate(() => [...document.querySelectorAll('table')].map(t => t.querySelectorAll('tbody tr').length + ':' + t.innerText.slice(0, 300))))
      await shot(page, `${mode}-a-lostload-unknown`)
      await ctx.close()
    }
    if (process.env.ONLY_A) continue
    if (!process.env.ONLY_E) {
    // (b) LostLoad with a recorded 0 (the real payload): the empty state
    {
      const { ctx, page } = await newPage(browser, mode, {})
      await openTab(page, 'lostload')
      await page.locator('[data-testid="lostload-empty-copy"]').waitFor({ timeout: 20_000 })
      await shot(page, `${mode}-b-lostload-real-zero`)
      await ctx.close()
    }
    // (b2) LostLoad with a null total and no shed series: "not recorded"
    {
      const nullTotal = { ...real, total_mwh: null, total_cost_eur: null }
      const { ctx, page } = await newPage(browser, mode, { lostLoad: nullTotal })
      await openTab(page, 'lostload')
      await page.locator('[data-testid="lostload-not-recorded"]').waitFor({ timeout: 20_000 })
      await shot(page, `${mode}-b2-lostload-null-total-empty`)
      await ctx.close()
    }
    // (c) Overview (Aggregated) with unknown lost-load KPIs and VOLL bars left out
    {
      const { ctx, page } = await newPage(browser, mode, { lostLoad: unknownLostLoad(real) })
      await openTab(page, 'overview')
      const t = await tabs(page)
      console.log(`[${mode}] tabs:`, t.join(' '))
      if (t.includes('results-tab-overview')) {
        await page.getByText('—: not recorded by this solve').first().waitFor({ timeout: 30_000 })
        await shot(page, `${mode}-c-overview-kpis`)
        const voll = page.locator('[data-testid="economics-voll-not-recorded"]')
        await voll.waitFor({ timeout: 20_000 })
        await voll.evaluate(el => el.scrollIntoView({ block: 'center' }))
        await page.waitForTimeout(800)
        await shot(page, `${mode}-c2-overview-economics-voll`)
      } else {
        await shot(page, `${mode}-c-overview-not-offered`)
      }
      await ctx.close()
    }
    // (d) Emissions with a binding cap whose shadow price was not recorded
    {
      const { ctx, page } = await newPage(browser, mode, { emissions: capsNoShadow(realEm) })
      await openTab(page, 'emissions')
      await page.getByText('—: not recorded by this solve').first().waitFor({ timeout: 20_000 })
      await shot(page, `${mode}-d-emissions-cap-no-shadow-price`)
      await ctx.close()
    }
    }
    // (e) Summary headline + cards with unknown lost load and shadow price
    {
      const { ctx, page } = await newPage(browser, mode, {
        lostLoad: { ...real, total_mwh: null, total_cost_eur: null, voll_eur_per_mwh: null },
        emissions: capsNoShadow(realEm) })
      await page.getByText('—: not recorded by this solve').first().waitFor({ timeout: 20_000 })
      await page.getByText(/a year/).first().waitFor({ timeout: 60_000 })
      await page.locator('[data-testid="summary-capacity"] table').waitFor({ timeout: 60_000 })
      await page.waitForTimeout(1500)
      await shot(page, `${mode}-e-summary-headline`)
      await ctx.close()
    }
  }
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
