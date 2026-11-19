// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * guided-results 02b (PR #1724) shots: "Why this number" open under the study's figures on Guided Results
 * (section 10), data-centre and microgrid templates, dark and light, in each state.
 *
 *   prep   create "Why DC" (eh_datacenter) and "Why MG" (eh_microgrid) when missing, and run each one's
 *          study once through Guided's own "Run study" (section 9) when it has none.
 *   shots  section 10 with the disclosure open. Only these are intercepted with page.route:
 *          GET /api/results/eh_study/why/{figure}  bodies from src/pages/hubDesign/cards/__fixtures__/ehStudyWhy.json,
 *            with the fixture's one assumed step replaced by steps for the study's own assumed rows;
 *          GET /api/results/eh_study (only when the study record carries no `assumptions`): the real body plus
 *            template-fitting rows like tests/fixtures/eh_study_run_assumptions.json.
 *          Everything else is the real app.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 * Usage: node scripts/why-this-number-shots.mjs [prep] [shots] [--out dir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const args = process.argv.slice(2)
const outAt = args.indexOf('--out')
const OUT = path.resolve(outAt >= 0 ? args[outAt + 1] : path.join(here, '..', '..', '..', '.scratch', 'guided-results', 'shots-02b'))
const picked = args.filter((a, i) => !a.startsWith('--') && (outAt < 0 || i !== outAt + 1))
const PHASES = picked.length ? picked : ['prep', 'shots']
const STUDY_WAIT_MS = Number(process.env.STUDY_WAIT_MS || 900_000)
const FIXTURE = JSON.parse(await fs.readFile(
  path.join(here, '..', 'src', 'pages', 'hubDesign', 'cards', '__fixtures__', 'ehStudyWhy.json'), 'utf8'))

const PROJECTS = {
  dc: { name: 'Why DC', template: 'eh_datacenter' },
  mg: { name: 'Why MG', template: 'eh_microgrid' },
}
// Used only when the real study record has no `assumptions` (shape of tests/fixtures/eh_study_run_assumptions.json).
const NERC = 'about NERC GADS 2019–2023'
const BESS = { label: 'Outage rate of battery storage units', value: 0.02, shown: '2 % forced outage rate, repair 24 h',
  source: 'carrier_default', section: 'assets', card: 'reliability', component: 'StorageUnit', field: 'outage_rate_value',
  reason: 'the typical value for battery units, about utility-scale BESS availability surveys (forced outage rate; sparse public data)' }
const DRAWS = { key: 'mc_draws', label: 'Simulation runs per reliability check', value: 500, shown: '500 runs', source: 'default',
  reason: 'the engine default', section: 'scenarios', card: 'reliability', component: '', entity: '', field: 'mc_draws' }
const FALLBACK_ROWS = {
  dc: [
    { key: 'outage_rate:Generator:gas', label: 'Outage rate of gas generating units (genset_1, genset_2, genset_3, genset_4)',
      value: 0.05, shown: '5 % forced outage rate on demand, repair 50 h', source: 'carrier_default',
      reason: `the typical value for gas units, ${NERC} gas CT/CC fleet class average (forced outage rate on demand)`,
      section: 'assets', card: 'reliability', component: 'Generator', entity: 'genset_1, genset_2, genset_3, genset_4',
      field: 'outage_rate_value' },
    { ...BESS, key: 'outage_rate:StorageUnit:battery', label: `${BESS.label} (ups_battery)`, entity: 'ups_battery' },
    DRAWS,
  ],
  mg: [
    { key: 'mttr:Generator:diesel', label: 'Repair time of diesel generating units (diesel_1, diesel_2, diesel_3)', value: 50.0,
      shown: '50 h', source: 'carrier_default',
      reason: `the typical value for oil units, ${NERC} oil/petroleum fleet class average (forced outage rate on demand)`,
      section: 'assets', card: 'reliability', component: 'Generator', entity: 'diesel_1, diesel_2, diesel_3', field: 'mttr_hours' },
    { ...BESS, key: 'outage_rate:StorageUnit:battery', label: `${BESS.label} (island_battery)`, entity: 'island_battery' },
    DRAWS,
  ],
}

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []

async function open(auth, project, theme, height = 1000, scale = 2) {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height },
    deviceScaleFactor: scale, reducedMotion: 'reduce' })
  await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await ctx.addInitScript(({ project, theme }) => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', 'guided')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:theme', theme)
  }, { project, theme })
  const page = await ctx.newPage()
  page.setDefaultTimeout(30000)
  page.setDefaultNavigationTimeout(60000)
  page.on('pageerror', e => problems.push(`${project}-${theme}: page error: ${e.message}`))
  await page.goto(`${BASE}/`, { timeout: 60000 })
  await page.waitForLoadState('networkidle', { timeout: 60000 })
  // The backend holds one live network: switch it to this project before the app reads it.
  await page.evaluate(async ({ url, name }) => {
    const { projectsApi } = await import(url)
    await projectsApi.activate(name)
  }, { url: '/src/api/projects.ts', name: project })
  await page.reload({ timeout: 60000 }); await page.waitForLoadState('networkidle', { timeout: 60000 })
  if (new URL(page.url()).pathname.startsWith('/projects')) {
    await page.getByText('Resume project').first().click()
    await page.waitForURL(u => !u.pathname.startsWith('/projects'), { timeout: 60000 })
    await page.waitForLoadState('networkidle', { timeout: 60000 })
  }
  await page.waitForTimeout(3000)
  return { ctx, page }
}

async function show(page, section) {
  const view = page.getByTestId(`section-view-${section}`)
  for (let attempt = 0; ; attempt++) {
    await page.evaluate(async (section) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().setUiMode('guided', { explicit: true })
      useUIStore.getState().setFieldDrawer(null)
      useUIStore.getState().setSlidePanel('hubDesign')
      useUIStore.getState().setActiveSection(section)
    }, section)
    await page.waitForLoadState('networkidle', { timeout: 60000 })
    await page.waitForTimeout(1500)
    if (await view.isVisible()) break
    if (attempt === 4) throw new Error(`section ${section} did not open`)
  }
  return view
}

const studyOf = page => page.evaluate(async () => {
  const { resultsApi } = await import('/src/api/simulation.ts')
  return await resultsApi.getEhStudy({ quiet: true })
})

/** The why body for one state, built on the real route's body (`real`, read for the open project):
 *  `real` (the record has no `assumptions`, so the route says "not compared") is served as is; `current` and
 *  `out_of_date` replace its one uncompared `assumptions` step with a fresh step per assumed row, and `out_of_date`
 *  marks the draws changed (500 → 1000) the way the fixture's captured stale walk does. */
const ASSUMED = 'input:eh_study/assumption/'
function whyBody(state, real, rows) {
  const body = structuredClone(real)
  if (state === 'real') return body
  const at = body.steps.findIndex(s => s.node === 'input:eh_study/assumptions')
  const assumed = rows.map(r => {
    const unit = r.entity && !r.entity.includes(', ') ? `/${r.entity}` : ''
    return { node: `${ASSUMED}${r.key}${unit}`, kind: 'input', label: r.label, value: r.value, unit: null, state: 'fresh',
      stamp: null, stamp_missing: [], broken: null,
      via: { edge: 'derived_from', from: 'run:study', recorded: { label: r.label, recorded: r.value } } }
  })
  body.steps.splice(at >= 0 ? at : body.steps.length, at >= 0 ? 1 : 0, ...assumed)
  body.outcome.uncompared = (body.outcome.uncompared ?? []).filter(n => n !== 'input:eh_study/assumptions')
  if (state === 'out_of_date') {
    const fx = FIXTURE.out_of_date.expected_shortfall.body.steps.find(s => s.node === 'input:eh_study/mc/draws')
    const draws = body.steps.find(s => s.node === 'input:eh_study/mc/draws')
    Object.assign(draws, { state: 'stale', broken: { ...fx.broken, compared: { ...fx.broken.compared, recorded: draws.value } } })
    body.outcome.state = 'stale'
  } else {
    const left = body.outcome.uncompared.length > 0
    body.outcome.state = left ? 'uncompared' : 'fresh'
    if (!left) for (const s of body.steps) if (s.state === 'uncompared') s.state = 'fresh'
  }
  body.outcome.steps_total = body.steps.length
  return body
}

try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE, { timeout: 60000 })
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/, { timeout: 60000 })
  await login.evaluate(async (projects) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const have = (await projectsApi.list()).map(p => p.name ?? p)
    for (const p of projects) if (!have.includes(p.name)) await projectsApi.createFromTemplate(p.template, p.name)
  }, Object.values(PROJECTS))
  const auth = await owner.storageState()
  await owner.close()

  if (PHASES.includes('prep')) {
    for (const [short, p] of Object.entries(PROJECTS)) {
      const { ctx, page } = await open(auth, p.name, 'dark', 1000, 1)
      let study = await studyOf(page)
      if (study?.status !== 'done') {
        await show(page, 'readiness')
        const start = page.getByTestId('hub-run-start')
        await start.waitFor({ timeout: 30000 })
        await page.waitForFunction(() => !document.querySelector('[data-testid="hub-run-start"]')?.disabled,
          null, { timeout: 60000 })
        await start.click()
        const t0 = Date.now()
        while (Date.now() - t0 < STUDY_WAIT_MS) {
          await page.waitForTimeout(5000)
          study = await studyOf(page)
          if (study && study.status !== 'running' && study.status !== 'pending') break
        }
        console.log(`${short}: study ${study?.status} after ${((Date.now() - t0) / 1000).toFixed(0)} s`)
      }
      console.log(`${short}: assumptions rows ${Array.isArray(study?.assumptions) ? study.assumptions.length : 'none'}`)
      await ctx.close()
    }
  }

  if (PHASES.includes('shots')) {
    // [project, theme, figure, state]; real = the route's own body, other_project = no interception of the why route.
    const SHOTS = []
    for (const short of ['dc', 'mg']) {
      for (const theme of ['dark', 'light']) {
        SHOTS.push([short, theme, 'expected_shortfall', 'current'])
        SHOTS.push([short, theme, 'verdict', 'current'])
      }
    }
    SHOTS.push(['dc', 'dark', 'design_cost', 'current'])
    SHOTS.push(['dc', 'dark', 'expected_shortfall', 'out_of_date'])
    SHOTS.push(['mg', 'light', 'verdict', 'out_of_date'])
    SHOTS.push(['dc', 'light', 'verdict', 'real'])
    SHOTS.push(['mg', 'dark', 'verdict', 'not_run'])
    SHOTS.push(['dc', 'light', 'expected_shortfall', 'failed'])
    SHOTS.push(['mg', 'dark', 'expected_shortfall', 'other_project'])

    for (const [short, theme, figure, state] of SHOTS) {
      const p = PROJECTS[short]
      const { ctx, page } = await open(auth, p.name, theme, 1800)
      const real = await studyOf(page)
      if (real?.status !== 'done') throw new Error(`${p.name}: no finished study (run the prep phase)`)
      const realRows = Array.isArray(real?.assumptions) && real.assumptions.length ? real.assumptions : null
      const rows = realRows ?? FALLBACK_ROWS[short]
      if (!realRows) {
        await ctx.route(u => /\/api\/results\/eh_study$/.test(u.pathname), async r => {
          if (r.request().method() !== 'GET') return r.fallback()
          const res = await r.fetch()
          if (res.status() !== 200) return r.fulfill({ response: res })
          const body = await res.json()
          body.assumptions = rows
          await r.fulfill({ response: res, json: body })
        })
      }
      if (state !== 'other_project') {
        await ctx.route(u => /\/api\/results\/eh_study\/why\//.test(u.pathname), async r => {
          if (state === 'failed') return r.fulfill({ status: 500, json: { detail: 'Internal Server Error' } })
          if (state === 'not_run') {
            const c = FIXTURE.not_run.verdict
            return r.fulfill({ status: c.status, json: c.body })
          }
          // The real walk, for the open project (project_id dropped: see the report on the name vs id).
          const url = new URL(r.request().url()); url.searchParams.delete('project_id')
          const res = await r.fetch({ url: url.toString() })
          if (res.status() !== 200) throw new Error(`real why ${url.pathname}: ${res.status()}`)
          await r.fulfill({ response: res, json: whyBody(state, await res.json(), rows) })
        })
      }
      await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
      await show(page, 'results')
      const id = `hub-why-${figure}`
      const box = page.getByTestId(id).first()
      await box.waitFor({ timeout: 30000 })
      await page.getByTestId(`${id}-toggle`).click()
      const failed = ['failed', 'not_run', 'other_project'].includes(state)
      await page.getByTestId(failed ? `${id}-failed` : `${id}-outcome`).waitFor({ timeout: 30000 })
      await page.waitForTimeout(600)
      await box.evaluate(n => n.scrollIntoView({ block: 'center' }))
      await page.waitForTimeout(500)
      // Frame: the headline's two tiles (expected shortfall, design cost), or the verdict block, with a margin.
      const host = figure === 'verdict' ? box.locator('xpath=..') : page.getByTestId('hub-results-headline')
      const b = await host.boundingBox()
      const vp = page.viewportSize()
      const pad = 14
      const x = Math.max(0, b.x - pad), y = Math.max(0, b.y - pad)
      const clip = { x, y, width: Math.min(vp.width - x, b.width + 2 * pad), height: Math.min(vp.height - y, b.height + 2 * pad) }
      if (b.y < 0 || b.y + b.height > vp.height) problems.push(`${short}-${theme}-${figure}-${state}: frame outside the viewport`)
      const file = path.join(OUT, `pr1724-${short}-${theme}-${figure.replaceAll('_', '-')}-${state.replaceAll('_', '-')}.png`)
      await page.screenshot({ path: file, clip })
      const text = await box.innerText()
      if (/input:|derived:|run:study|eh_study|assumption\//.test(text)) problems.push(`${file}: raw id in text`)
      console.log(file)
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log(`shots in ${OUT}`)
