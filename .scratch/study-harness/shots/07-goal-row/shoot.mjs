// Shots for #1257's goal row (UX (13) ask 4). Needs Vite serving pypsa-gui/frontend
// on PORT (default 5199); copies harness.tsx into src/ and removes it in finally.
// Run: node shoot.mjs   (with NODE_PATH pointing at a playwright install)
import { createRequire } from 'node:module'
import { copyFileSync, readFileSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')  // NODE_PATH=$(npm root -g)
const here = dirname(fileURLToPath(import.meta.url))
const tmp = resolve(here, '../../../../pypsa-gui/frontend/src/goal-row.tmp.tsx')
// The backend's default SolverConfig (SolverConfigSchema of SolverConfig()), for Expert's Solver settings.
const SOLVER_CONFIG = JSON.parse(readFileSync(resolve(here, 'solver_config.json'), 'utf8'))
const base = `http://127.0.0.1:${process.env.PORT ?? 5199}`
const GOAL = (lole, src) => ({
  target_lole_h: { value: lole, source: src }, ens_cap_permyriad: { value: null, source: 'default' },
  mc_draws: { value: 500, source: 'default' }, site_archetype: { value: 'strong_grid', source: 'default' } })
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><script type="module">
import R from "/@react-refresh";R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};
window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true</script></head>
<body><div id="root"></div><script type="module" src="/src/goal-row.tmp.tsx"></script></body></html>`
const STATES = [
  ['01-goal-stored', { value: 3, unit: 'h/yr', source: 'stored' }, GOAL(3, 'stored')],
  ['02-goal-template', { value: 2, unit: 'h/yr', source: 'template' }, GOAL(2, 'template')],
  ['03-no-goal', { value: null, unit: 'h/yr', source: 'default' }, GOAL(null, 'default')],
]
copyFileSync(resolve(here, 'harness.tsx'), tmp)
const browser = await chromium.launch()
try {
  for (const mode of ['guided', 'expert']) {
    for (const [name, cardGoal, stored] of STATES) {
      const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 2 })
      page.on('pageerror', e => console.log(name, mode, 'pageerror', e.message))
      await page.route('**/goal-row.html*', r => r.fulfill({ contentType: 'text/html', body: PAGE }))
      await page.route(`${base}/api/**`, r => {
        const url = r.request().url()
        if (url.includes('/view')) return r.fulfill({ json: { monitor: { unattended: { available: true }, email: { available: false }, budgets: { cost_eur: { limit: 5 } } } } })
        if (url.endsWith('/simulation/solver_config')) return r.fulfill({ json: SOLVER_CONFIG })
        if (url.endsWith('/reliability_goal')) return r.fulfill({ json: stored })
        return r.fulfill({ status: 404, json: { detail: 'not mocked' } })
      })
      await page.goto(`${base}/goal-row.html?mode=${mode}&goal=${encodeURIComponent(JSON.stringify(cardGoal))}`)
      await page.waitForSelector('[data-testid=envelope-goal]')
      await page.waitForTimeout(500)
      await page.screenshot({ path: resolve(here, `${name}-${mode}.png`) })
      if (cardGoal.value === null) {
        await page.click('[data-testid=envelope-goal-fix]')
        await page.waitForTimeout(1500)
        await page.screenshot({ path: resolve(here, `04-editor-via-link-${mode}.png`) })
      }
      await page.close()
    }
  }
} finally {
  await browser.close()
  rmSync(tmp, { force: true })
}
