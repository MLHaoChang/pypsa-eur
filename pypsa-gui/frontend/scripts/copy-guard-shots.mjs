// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
//
// SPDX-License-Identifier: MIT
//
// expert-catalogue 02c end-to-end (raw-name guard): the copy 02c changed, on the
// real API and Vite, in Guided and Expert × light and dark.
//  1. A blank project: the capacity-bounds empty state (it names the Properties
//     panel's "Extendable" and no model name).
//  2. With TEMPLATE set (default Island Microgrid) and a solve: the Results tabs
//     whose headers or help 02c reworded (Dispatch, Storage cycling, Asset
//     economics, Prices, Capacity expansion, Emissions). A tab Guided hides is
//     recorded as hidden, not as a failure, and the flag that hides it is printed.
// Every shot is downscaled with scripts/coord/shots.sh; read the .small files.
// Exit 1 when a check fails.
//
//   BASE=http://127.0.0.1:5173 OUT=<dir> [TEMPLATE=eh_microgrid | TEMPLATE=] node scripts/copy-guard-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

// Resolves the pre-installed Playwright through NODE_PATH, as the other shot scripts do.
const { chromium } = createRequire(import.meta.url)('playwright')

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS_SH = path.resolve(HERE, '../../../scripts/coord/shots.sh')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = process.env.OUT || path.resolve('copy-guard-shots')
const TEMPLATE = process.env.TEMPLATE ?? 'eh_microgrid'
const BLANK = process.env.BLANK_PROJECT || 'copy guard blank'
const TEMPLATED = process.env.TEMPLATE_PROJECT || 'copy guard template'
// Results tabs whose copy 02c changed, with a text the tab shows after the change.
const RESULT_TABS = [
  ['dispatch', 'Capacity (MW)'],
  ['storage', 'Power (MW)'],
  ['economics', null],
  ['prices', null],
  ['capex', null],
  ['emissions', null],
]
// Changed copy no state here reaches, and why (listed in result.json).
const UNSHOT = [
  ['pages/CompareView.tsx', 'needs two solved projects in the compare rail'],
  ['components/MapConnectDialog.tsx, pages/TopologyCanvas.tsx', 'the connect dialog opens on a drag between two buses'],
  ['components/VintagePeriodBoundsModal.tsx', 'needs a multi-period project with an extendable asset'],
  ['pages/modelHorizon/StepYears.tsx', 'the Model horizon wizard, step 2'],
  ['pages/results/SiteConnectionForm.tsx, pages/results/adequacy.tsx, pages/results/ReserveMarginPanel.tsx', 'need a network without a one-way Link, zone data or a reserve-margin study'],
  ['pages/IssuesPanel.tsx', 'needs a carrier with zero CO2 and a catalogue value'],
  ['pages/results/investment/FinanceInputsEditor.tsx', 'needs a US finance pack'],
]
const BAD = /\b(?:p_nom|s_nom|e_nom|v_nom|p_set|q_set|p_max_pu|co2_emissions|curtailment_cost|build_year)\b|_extendable|=True/
await fs.mkdir(OUT, { recursive: true })

const browser = await chromium.launch()
const failures = []
const results = []
const shots = []
const shoot = async (p, name) => { const f = path.join(OUT, `${name}.png`); await p.screenshot({ path: f }); shots.push(f) }
const store = (page, src, arg) => page.evaluate(async ({ src, arg }) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  return (new Function('s', 'a', src))(useUIStore.getState(), arg)
}, { src, arg })
let expertOnlyFlag = null
try {
  const setup = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await setup.newPage()
  page.setDefaultTimeout(60000)
  const { email, password } = devCredentials()
  await page.goto(BASE)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
  await page.waitForLoadState('networkidle')
  // Which flag makes a Results tab Expert-only: read it from the module itself.
  expertOnlyFlag = await page.evaluate(async () => {
    const { TABS } = await import('/src/pages/Results.tsx')
    return { flag: 'TABS[].expertOnly in pages/Results.tsx', expertOnly: TABS.filter(t => t.expertOnly).map(t => t.id) }
  })
  // A blank project (the Projects page's "Blank" path: reset, then save under a name).
  await page.evaluate(async name => {
    const { networkApi } = await import('/src/api/network.ts')
    const { projectsApi } = await import('/src/api/projects.ts')
    await networkApi.resetNetwork()
    await projectsApi.save(name, true)
  }, BLANK)
  if (TEMPLATE) {
    const ran = await page.evaluate(async ({ id, name }) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const { simulationApi } = await import('/src/api/simulation.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(p => p.name === name)) await projectsApi.activate(name)
      else await projectsApi.createFromTemplate(id, name)
      await simulationApi.run()
      for (let i = 0; i < 100; i++) {
        await new Promise(r => setTimeout(r, 3000))
        const s = await simulationApi.getStatus().catch(() => null)
        if (s && !s.running) return `${s.condition} ${s.objective}`
      }
      return 'timeout'
    }, { id: TEMPLATE, name: TEMPLATED }).catch(e => `run failed: ${e.message.split('\n')[0]}`)
    if (/failed|timeout/.test(ran)) failures.push(`${TEMPLATE}: ${ran}`)
  }
  const state = await setup.storageState()
  await setup.close()

  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    for (const project of [BLANK, ...(TEMPLATE ? [TEMPLATED] : [])]) {
      const key = `${mode}-${theme}-${project === BLANK ? 'blank' : 'template'}`
      const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2,
        reducedMotion: 'reduce', storageState: state })
      await ctx.addInitScript(({ mode, project, theme }) => {
        localStorage.setItem('network-diagram:current-project', project)
        localStorage.setItem('network-diagram:assistant-dock', 'closed')
        localStorage.setItem('network-diagram:ui-mode', mode)
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
        localStorage.setItem('network-diagram:theme', theme)
        localStorage.setItem('network-diagram:theme-schema', '2')
      }, { mode, project, theme })
      const p = await ctx.newPage()
      p.setDefaultTimeout(20000)
      p.on('pageerror', e => failures.push(`${key}: page error: ${e.message}`))
      try {
        await p.goto(`${BASE}/app`)
        await p.evaluate(async name => { const { projectsApi } = await import('/src/api/projects.ts'); await projectsApi.activate(name) }, project)
        await p.reload()
        await p.getByRole('group', { name: 'Interface mode' }).waitFor()
        await p.waitForTimeout(1500)
        const skipTour = p.getByRole('button', { name: 'Skip tour' })
        if (await skipTour.isVisible().catch(() => false)) await skipTour.click()
        if (project === BLANK) {
          await store(p, 's.setSlidePanel(a)', 'capacityBounds')
          const empty = p.getByTestId('capacity-bounds-empty')
          const shown = await empty.waitFor({ timeout: 10000 }).then(() => true).catch(() => false)
          const text = shown ? (await empty.innerText()).replace(/\s+/g, ' ').trim() : null
          await shoot(p, `${key}-capacity-bounds-empty`)
          const ok = !!text && text.includes('“Extendable”') && !BAD.test(text)
          if (!ok) failures.push(`${key}: capacity-bounds empty state ${JSON.stringify(text)}`)
          results.push({ key, surface: 'capacity-bounds empty state', text, mode: await p.evaluate(() => localStorage.getItem('network-diagram:ui-mode')), ok })
        } else {
          for (const [tab, expect] of RESULT_TABS) {
            const hidden = mode === 'guided' && expertOnlyFlag.expertOnly.includes(tab)
            await store(p, "s.requestResultsTab(a); s.setSlidePanel('results')", tab)
            await p.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
            await p.waitForTimeout(1200)
            await shoot(p, `${key}-results-${tab}`)
            const body = await p.locator('body').innerText()
            const raw = BAD.exec(body)?.[0] ?? null
            const found = expect ? body.includes(expect) : null
            const ok = hidden || (!raw && found !== false)
            if (!ok) failures.push(`${key}: results ${tab}: raw ${raw}, "${expect}" shown ${found}`)
            results.push({ key, surface: `Results ${tab}`, hidden, raw, found, ok })
          }
        }
      } catch (e) {
        failures.push(`${key}: ${e.message.split('\n')[0]}`)
        await shoot(p, `${key}-error`).catch(() => {})
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
let small = []
if (shots.length) {
  try { small = execFileSync('bash', [SHOTS_SH, ...shots], { encoding: 'utf8' }).trim().split('\n') }
  catch (e) { failures.push(`shots.sh: ${String(e.message).split('\n')[0]}`) }
}
await fs.writeFile(path.join(OUT, 'result.json'), JSON.stringify({ expertOnlyFlag, results, unshot: UNSHOT, small, failures }, null, 1))
console.log(`Expert-only tabs: ${expertOnlyFlag?.flag} → ${expertOnlyFlag?.expertOnly.join(', ')}`)
for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.key}: ${r.surface}${r.hidden ? ' (hidden in Guided)' : ''}${r.text ? ` "${r.text}"` : ''}${r.raw ? ` raw ${r.raw}` : ''}`)
for (const [f, why] of UNSHOT) console.log(`not shot: ${f}: ${why}`)
for (const f of small) console.log(`small: ${f}`)
for (const f of failures) console.log(`FAIL ${f}`)
process.exit(failures.length ? 1 : 0)
