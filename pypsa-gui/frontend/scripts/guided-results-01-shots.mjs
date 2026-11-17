// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * guided-results 01 screenshot harness: Guided section 10 (Results & reports) and Expert
 * Results > Summary on the real app, dark and light: `<proj>-<theme>-<state>.jpg`.
 *   dc  (Data Center Energy Hub, study done): ready, ready-tiles.  dcx: expert-summary.
 *   dcstale: stale (after one generator's p_nom is nudged, so the study is out of date).
 *   dcunknown: freshness-unknown (the study and review answers mocked to `edited_since_study: null`).
 *   dcn (Data Center Energy Hub not run): not-run.
 *   mg  (Island Microgrid, created when missing): not-run, then the Guided "Run study" click
 *       in section 9, running (dark, when caught), and ready once the study is done (or
 *       later with mgready, when the study outlasts STUDY_WAIT_MS).
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 * Usage: node scripts/guided-results-01-shots.mjs [dc|dcx|dcstale|dcunknown|dcn|mg|mgready ...] [--out dir]
 * env: BASE, DC_PROJECT (the dc/dcx/dcstale/dcunknown project, default "Data Center Energy Hub")
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
  : path.join(here, '..', '..', '..', '.scratch', 'guided-results', 'shots-01'))
const picked = args.filter((a, i) => !a.startsWith('--') && (outAt < 0 || i !== outAt + 1))
const PHASES = picked.length ? picked : ['dc', 'dcx', 'dcstale', 'dcunknown', 'dcn', 'mg', 'mgready']
const DC = process.env.DC_PROJECT || 'Data Center Energy Hub'
const DCN = 'Data Center Energy Hub not run'
const MG = 'Island Microgrid'
const THEMES = ['dark', 'light']
const STUDY_WAIT_MS = Number(process.env.STUDY_WAIT_MS || 420_000)
let mgDone = false
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []

async function open(auth, project, theme, height = 800) {
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

const shoot = (page, file) => page.screenshot({ path: path.join(OUT, file), type: 'jpeg', quality: 80 })

async function scrollTo(page, testId) {
  const el = page.getByTestId(testId).first()
  try { await el.waitFor({ timeout: 30000 }) } catch (e) {
    await page.screenshot({ path: path.join(OUT, `debug-missing-${testId}.jpg`), type: 'jpeg', quality: 70 })
    throw e
  }
  await el.evaluate(n => {
    n.scrollIntoView({ block: 'start' })
    // Leave the heading above the element in view too.
    for (let p = n.parentElement; p; p = p.parentElement) {
      if (p.scrollHeight > p.clientHeight && getComputedStyle(p).overflowY !== 'visible') { p.scrollTop -= 40; break }
    }
  })
  await page.waitForTimeout(800)
}

/** Section 10 of a project whose study is done: the headline and chart, then the chapter tiles. */
async function shootReady(page, short, theme) {
  await show(page, 'results')
  await scrollTo(page, 'hub-results-headline')
  await page.getByTestId('hub-results-reliability-chart').first().waitFor({ timeout: 30000 })
  await shoot(page, `${short}-${theme}-ready.jpg`)
  await scrollTo(page, 'hub-chapters')
  await shoot(page, `${short}-${theme}-ready-tiles.jpg`)
}

async function shootSection10(page, file) {
  const view = await show(page, 'results')
  await view.evaluate(n => n.scrollIntoView({ block: 'start' }))
  await page.waitForTimeout(800)
  await shoot(page, file)
}

const studyStatus = page => page.evaluate(async () => {
  const { resultsApi } = await import('/src/api/simulation.ts')
  const s = await resultsApi.getEhStudy({ quiet: true })
  return s?.status ?? null
})

try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE, { timeout: 60000 })
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/, { timeout: 60000 })
  if (PHASES.includes('mg') || PHASES.includes('mgready')) {
    await login.evaluate(async (name) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const have = (await projectsApi.list()).map(p => p.name ?? p)
      if (!have.includes(name)) await projectsApi.createFromTemplate('eh_microgrid', name)
    }, MG)
  }
  const auth = await owner.storageState()
  await owner.close()

  if (PHASES.includes('dc')) {
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, DC, theme)
      await shootReady(page, 'dc', theme)
      await ctx.close()
    }
  }
  if (PHASES.includes('dcx')) {
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, DC, theme)
      // Expert Results > Summary: the headline strip.
      await page.evaluate(async () => {
        const { useUIStore } = await import('/src/store/uiStore.ts')
        useUIStore.getState().setUiMode('expert', { explicit: true })
        useUIStore.getState().setSlidePanel('results')
        useUIStore.getState().requestResultsTab('summary')
      })
      await page.waitForLoadState('networkidle', { timeout: 60000 })
      await scrollTo(page, 'headline-strip')
      await shoot(page, `dc-${theme}-expert-summary.jpg`)
      await ctx.close()
    }
  }
  if (PHASES.includes('dcstale')) {
    // Out of date: nudge one generator's p_nom, then section 10 again.
    for (const theme of THEMES) {
      // Taller viewport: the out-of-date banner and the chart's pill in one frame.
      const { ctx, page } = await open(auth, DC, theme, 1000)
      if (theme === 'dark') {
        // The app may still be settling its project switch: retry when the page navigates under us.
        const nudge = () => page.evaluate(async () => {
          const { networkApi } = await import('/src/api/network.ts')
          const gens = await networkApi.getGenerators()
          const g = gens.find(r => typeof r.p_nom === 'number' && r.p_nom > 0) ?? gens[0]
          await networkApi.updateGenerator(g.name, { name: g.name, bus: g.bus, p_nom: (g.p_nom || 1) * 1.01 })
          return `${g.name}: ${g.p_nom} -> ${(g.p_nom || 1) * 1.01}`
        })
        let edited = null
        for (let attempt = 0; edited === null; attempt++) {
          try { edited = await nudge() } catch (e) {
            if (attempt === 4 || !/context was destroyed|navigation/.test(String(e))) throw e
            console.log(`stale edit: page navigated (${new URL(page.url()).pathname}), retrying`)
            await page.waitForLoadState('networkidle', { timeout: 60000 }); await page.waitForTimeout(2000)
          }
        }
        console.log(`stale edit: ${edited}`)
        await page.reload({ timeout: 60000 }); await page.waitForLoadState('networkidle', { timeout: 60000 })
        await page.waitForTimeout(2000)
      }
      await shootSection10(page, `dc-${theme}-stale.jpg`)
      const flag = await page.locator('[data-testid="hub-results-stale"], [data-testid="hub-results-edited"]').count()
      if (!flag) problems.push(`dc-${theme}-stale: neither hub-results-stale nor hub-results-edited shown`)
      await ctx.close()
    }
  }

  if (PHASES.includes('dcn')) {
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, DCN, theme)
      await shootSection10(page, `dcn-${theme}-not-run.jpg`)
      await ctx.close()
    }
  }

  if (PHASES.includes('mg')) {
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, MG, theme)
      await shootSection10(page, `mg-${theme}-not-run.jpg`)
      if (theme === 'light') { await ctx.close(); continue }
      // Run the study the way Guided does: section 9's "Run study".
      await show(page, 'readiness')
      const start = page.getByTestId('hub-run-start')
      await start.waitFor({ timeout: 30000 })
      await page.waitForFunction(() => !document.querySelector('[data-testid="hub-run-start"]')?.disabled,
        null, { timeout: 60000 })
      await start.click()
      const t0 = Date.now()
      try {
        await page.getByTestId('hub-run-running').waitFor({ timeout: 15000 })
        await show(page, 'results')
        await shoot(page, 'mg-dark-running.jpg')
        console.log(`running shot taken ${((Date.now() - t0) / 1000).toFixed(1)} s after the click`)
      } catch {
        console.log('running state not caught')
      }
      let status = null
      while (Date.now() - t0 < STUDY_WAIT_MS) {
        status = await studyStatus(page)
        if (status && status !== 'running' && status !== 'pending') break
        await page.waitForTimeout(5000)
      }
      console.log(`mg study status ${status} after ${((Date.now() - t0) / 1000).toFixed(0)} s`)
      mgDone = status === 'done'
      await ctx.close()
    }
  }
  if (PHASES.includes('dcunknown')) {
    // Freshness unknown (GK B F1): a study record with no captured revision answers
    // `edited_since_study: null`. A fresh install can't save one, so the payload is mocked (#1470).
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, DC, theme, 1100)
      await ctx.route(u => /\/api\/results\/eh_(study|review)(\?|$)/.test(u.pathname + u.search), async r => {
        const res = await r.fetch()
        const body = await res.json()
        if (body && typeof body === 'object') {
          body.edited_since_study = null
          if ('stale' in body) body.stale = false
        }
        await r.fulfill({ response: res, json: body })
      })
      await page.reload({ waitUntil: 'networkidle', timeout: 60000 })
      const view = await show(page, 'results')
      await page.getByTestId('hub-results-freshness-unknown').first().waitFor({ timeout: 30000 })
      await page.getByTestId('hub-results-reliability-chart').first().waitFor({ timeout: 30000 })
      await view.evaluate(n => n.scrollIntoView({ block: 'start' }))
      await page.waitForTimeout(800)
      await shoot(page, `dc-${theme}-freshness-unknown.jpg`)
      await ctx.close()
    }
  }
  // mgready: the study is done (this run's or an earlier one's, still running then).
  if (PHASES.includes('mgready') || mgDone) {
    for (const theme of THEMES) {
      const { ctx, page } = await open(auth, MG, theme)
      await shootReady(page, 'mg', theme)
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log(`shots in ${OUT}`)
