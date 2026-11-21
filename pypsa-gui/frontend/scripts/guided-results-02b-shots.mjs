// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * guided-results 02b screenshot harness: Guided section 10 (Results & reports) with the "Why this number"
 * disclosure open, on the real app, dark and light: `<proj>-<theme>-<state>.png`, clipped to section 10.
 *   setup: creates "Data Center Energy Hub" (dc), "Island Microgrid" (mg) and their "... not run" copies
 *          from templates when missing, then runs the Guided reliability study for dc and mg (section 9's
 *          "Run study"), waiting up to STUDY_WAIT_MS each.
 *   dc / mg, per state:
 *     not-compared: real. A study record today carries no `assumptions` (the runner does not record them yet),
 *       so the real walk reads "Not compared: Assumed inputs could not be checked" and lists no assumed row.
 *     current-mocked: the real why answer with that one unchecked step replaced by one recorded assumed row
 *       (`mc_draws` from backend/tests/fixtures/eh_study_run_assumptions.json, at the run's own value), and the
 *       study read given the same row, so the walk reads Current and the row shows its AssumedChip.
 *     failed-read-mocked: the why read answered 500.
 *     out-of-date: real, after one generator's p_nom is nudged (a real edit: these run last).
 *     out-of-date-assumed-mocked: that real stale answer with the assumed row injected as for current-mocked.
 *     not-run: the "... not run" project, no study (real).
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 * Usage: node scripts/guided-results-02b-shots.mjs [setup] [dc|mg ...] [--out dir]
 * env: BASE, THEME (dark|light, default both), ONLY (comma list of states), FIGURE (default expected_shortfall),
 *      STUDY_WAIT_MS
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
const OUT = path.resolve(outAt >= 0 ? args[outAt + 1]
  : path.join(here, '..', '..', '..', '.scratch', 'guided-results', 'shots-02b'))
const picked = args.filter((a, i) => !a.startsWith('--') && (outAt < 0 || i !== outAt + 1))
const PHASES = picked.length ? picked : ['dc', 'mg']
const THEMES = process.env.THEME ? [process.env.THEME] : ['dark', 'light']
const STATES = ['not-compared', 'current-mocked', 'failed-read-mocked', 'not-run', 'out-of-date', 'out-of-date-assumed-mocked']
const ONLY = process.env.ONLY ? process.env.ONLY.split(',') : STATES
const FIGURE = process.env.FIGURE || 'expected_shortfall'
const STUDY_WAIT_MS = Number(process.env.STUDY_WAIT_MS || 540_000)
const PROJECTS = {
  dc: { name: 'Data Center Energy Hub', template: 'eh_datacenter' },
  mg: { name: 'Island Microgrid', template: 'eh_microgrid' },
}
const notRun = short => `${PROJECTS[short].name} not run`
const ASSUMED_ROW = JSON.parse(await fs.readFile(
  path.join(here, '..', '..', 'backend', 'tests', 'fixtures', 'eh_study_run_assumptions.json'), 'utf8'))
  .find(r => r.key === 'mc_draws')
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
const WHY = /\/api\/results\/eh_study\/why\//
const STUDY = /\/api\/results\/eh_study$/
const ASSUMPTIONS = 'input:eh_study/assumptions'

/** Answers the why read with the real answer, its unchecked `assumptions` step replaced by ASSUMED_ROW's pin. */
async function injectAssumed(ctx) {
  let draws = ASSUMED_ROW.value
  await ctx.route(u => STUDY.test(u.pathname), async r => {
    const res = await r.fetch()
    const body = await res.json().catch(() => null)
    if (!body || typeof body !== 'object') return r.fulfill({ response: res })
    draws = body.mc_draws ?? body.request?.mc_draws ?? draws
    const row = { ...ASSUMED_ROW, value: draws, shown: `${draws} runs` }
    return r.fulfill({ response: res, json: { ...body, assumptions: [row] } })
  })
  await ctx.route(u => WHY.test(u.pathname), async r => {
    const res = await r.fetch()
    const body = await res.json().catch(() => null)
    if (!res.ok() || !body?.steps) return r.fulfill({ response: res })
    const at = body.steps.findIndex(s => s.node === ASSUMPTIONS)
    const drawsStep = body.steps.find(s => s.node === 'input:eh_study/mc/draws')
    const value = drawsStep?.value ?? draws
    const pin = { broken: null, kind: 'input', label: ASSUMED_ROW.label, node: `input:eh_study/assumption/${ASSUMED_ROW.key}`,
      stamp: null, stamp_missing: ['unit'], state: 'fresh', unit: null, value,
      via: { edge: 'derived_from', from: 'run:study', recorded: { label: ASSUMED_ROW.label, recorded: value } } }
    if (at >= 0) body.steps.splice(at, 1, pin); else body.steps.push(pin)
    const left = (body.outcome.uncompared ?? []).filter(n => n !== ASSUMPTIONS)
    body.outcome = { ...body.outcome, uncompared: left,
      state: body.outcome.state === 'uncompared' && left.length === 0 ? 'fresh' : body.outcome.state }
    return r.fulfill({ response: res, json: body })
  })
}

async function open(auth, project, theme, height = 1000) {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height },
    deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
  await page.waitForTimeout(3000)   // the app's own project switch settles first
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

/** Section 10, clipped to its view so the small copy stays readable. */
async function shoot(page, file) {
  const box = await page.getByTestId('section-view-results').boundingBox()
  const vp = page.viewportSize()
  const clip = box ? { x: Math.max(0, box.x), y: 0, width: Math.min(box.width, vp.width - Math.max(0, box.x)), height: vp.height } : undefined
  await page.screenshot({ path: path.join(OUT, file), clip })
  console.log(`shot ${file}`)
}

/** Opens "Why this number" on FIGURE (falling back to the verdict's) and scrolls it to the top. */
async function openWhy(page, file) {
  let id = `hub-why-${FIGURE}`
  if (!(await page.getByTestId(`${id}-toggle`).count())) id = 'hub-why-verdict'
  const toggle = page.getByTestId(`${id}-toggle`).first()
  try { await toggle.waitFor({ timeout: 30000 }) } catch (e) {
    problems.push(`${file}: no "Why this number" toggle`)
    return null
  }
  await toggle.click()
  await page.locator(`[data-testid="${id}-outcome"], [data-testid="${id}-failed"]`).first()
    .waitFor({ timeout: 30000 }).catch(() => problems.push(`${file}: disclosure did not finish reading`))
  await page.getByTestId(id).first().evaluate(n => {
    n.scrollIntoView({ block: 'start' })
    for (let p = n.parentElement; p; p = p.parentElement) {
      if (p.scrollHeight > p.clientHeight && getComputedStyle(p).overflowY !== 'visible') { p.scrollTop -= 200; break }
    }
  })
  await page.waitForTimeout(800)
  return id
}

async function check(page, id, file, want) {
  if (!id) return
  const out = page.getByTestId(`${id}-outcome`)
  const state = (await out.count()) ? await out.first().getAttribute('data-state') : 'failed'
  const assumed = await page.getByTestId(`${id}-assumed-row`).count()
  const text = await page.getByTestId(id).first().innerText()
  if (want && state !== want) problems.push(`${file}: disclosure state ${state}, wanted ${want}`)
  if (state !== 'failed' && !assumed && file.includes('-mocked')) problems.push(`${file}: no assumed row`)
  const raw = text.match(/\b(input:|derived:|run:|results:|eh_study|p_nom|mc\/|[a-z]+_[a-z_]+)\b/g)
  if (raw) problems.push(`${file}: engine-like words: ${[...new Set(raw)].join(', ')}`)
  console.log(`${file}: state=${state} assumed=${assumed}\n  ${text.replace(/\n+/g, ' | ').slice(0, 600)}`)
}

const studyStatus = page => page.evaluate(async () => {
  const { resultsApi } = await import('/src/api/simulation.ts')
  const s = await resultsApi.getEhStudy({ quiet: true })
  return s?.status ?? null
})

async function runStudy(auth, project) {
  const { ctx, page } = await open(auth, project, 'dark')
  try {
    const before = await page.evaluate(async () => {
      const { resultsApi } = await import('/src/api/simulation.ts')
      return resultsApi.getEhStudy({ quiet: true })
    }).catch(() => null)
    // A done study is reused unless an edit since made it out of date (the out-of-date shots make one).
    if (before?.status === 'done' && before.edited_since_study === false && !before.stale) {
      console.log(`${project}: study already done and current`); return
    }
    await show(page, 'readiness')
    const start = page.getByTestId('hub-run-start')
    await start.waitFor({ timeout: 30000 })
    await page.waitForFunction(() => !document.querySelector('[data-testid="hub-run-start"]')?.disabled,
      null, { timeout: 60000 })
    await start.click()
    const t0 = Date.now()
    let status = null
    while (Date.now() - t0 < STUDY_WAIT_MS) {
      await page.waitForTimeout(5000)
      status = await studyStatus(page).catch(() => null)
      if (status && status !== 'running' && status !== 'pending') break
    }
    console.log(`${project}: study status ${status} after ${((Date.now() - t0) / 1000).toFixed(0)} s`)
    if (status !== 'done') problems.push(`${project}: study ended ${status}`)
  } finally { await ctx.close() }
}

async function nudge(page) {
  const edit = () => page.evaluate(async () => {
    const { networkApi } = await import('/src/api/network.ts')
    const gens = await networkApi.getGenerators()
    const g = gens.find(r => typeof r.p_nom === 'number' && r.p_nom > 0) ?? gens[0]
    await networkApi.updateGenerator(g.name, { name: g.name, bus: g.bus, p_nom: (g.p_nom || 1) * 1.01 })
    return `${g.name}: ${g.p_nom} -> ${(g.p_nom || 1) * 1.01}`
  })
  for (let attempt = 0; ; attempt++) {
    try { console.log(`stale edit: ${await edit()}`); break } catch (e) {
      if (attempt === 4 || !/context was destroyed|navigation/.test(String(e))) throw e
      await page.waitForLoadState('networkidle', { timeout: 60000 }); await page.waitForTimeout(2000)
    }
  }
  await page.reload({ timeout: 60000 }); await page.waitForLoadState('networkidle', { timeout: 60000 })
  await page.waitForTimeout(2000)
}

async function shotState(auth, short, theme, state) {
  const file = `${short}-${theme}-${state}.png`
  const project = state === 'not-run' ? notRun(short) : PROJECTS[short].name
  const { ctx, page } = await open(auth, project, theme)
  try {
    if (state.endsWith('-mocked') && state !== 'failed-read-mocked') {
      await injectAssumed(ctx)
      await page.reload({ timeout: 60000 }); await page.waitForLoadState('networkidle', { timeout: 60000 })
    }
    if (state === 'failed-read-mocked') {
      await ctx.route(u => WHY.test(u.pathname), r => r.fulfill({ status: 500, json: { detail: 'boom' } }))
    }
    if (state === 'out-of-date' && theme === THEMES[0]) await nudge(page)
    await show(page, 'results')
    if (state === 'not-run') {
      const view = page.getByTestId('section-view-results')
      await view.evaluate(n => n.scrollIntoView({ block: 'start' }))
      const toggles = await page.locator('[data-testid^="hub-why-"][data-testid$="-toggle"]').count()
      if (toggles) await check(page, await openWhy(page, file), file, null)
      else console.log(`${file}: no "Why this number" toggle shown (no study)`)
      await page.waitForTimeout(800)
      await shoot(page, file)
      return
    }
    await page.getByTestId('hub-results-headline').first().waitFor({ timeout: 30000 }).catch(() => {})
    const id = await openWhy(page, file)
    await shoot(page, file)
    await check(page, id, file, { 'current-mocked': 'current', 'out-of-date': 'out_of_date',
      'out-of-date-assumed-mocked': 'out_of_date', 'not-compared': 'not_compared', 'failed-read-mocked': null }[state])
  } finally { await ctx.close() }
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
  const auth = await owner.storageState()
  if (PHASES.includes('setup')) {
    const want = Object.entries(PROJECTS).flatMap(([s, p]) => [[p.template, p.name], [p.template, notRun(s)]])
    await login.evaluate(async (want) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const have = (await projectsApi.list()).map(p => p.name ?? p)
      for (const [t, name] of want) if (!have.includes(name)) await projectsApi.createFromTemplate(t, name)
    }, want)
    await owner.close()
    for (const p of Object.values(PROJECTS)) await runStudy(auth, p.name)
  } else await owner.close()

  for (const short of ['dc', 'mg']) {
    if (!PHASES.includes(short)) continue
    for (const state of STATES) {
      if (!ONLY.includes(state)) continue
      for (const theme of THEMES) {
        try { await shotState(auth, short, theme, state) } catch (e) {
          problems.push(`${short}-${theme}-${state}: ${String(e).split('\n')[0]}`)
        }
      }
    }
  }
} finally {
  await browser.close()
}
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log(`shots in ${OUT}`)
