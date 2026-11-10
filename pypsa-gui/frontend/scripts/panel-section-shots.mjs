// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * LC-14 item 5 (UX condition, #1328): the grid study jumped to from chat. Route-mocked harness (as
 * intent-comparison-shots.mjs): the API on :8000 and Vite on :5173 run for the real sign-in; only the campus
 * routes are mocked. Applies the navigation the chat applies (applyUiNavigate) and checks the section heading
 * is fully visible below the panel's top edge.
 *
 * Usage: node scripts/panel-section-shots.mjs [outDir]
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'live-copilot', 'shots', 'lc14-section'))
const PROJECT = 'Section jump shots'

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = [], written = [], facts = []
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  const auth = await owner.storageState()
  await owner.close()

  for (const mode of ['expert', 'guided']) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => problems.push(`${mode}: page error: ${e.message}`))
    const state = { campus_yaml: 'campus:\n  name: shots\n', skipped: [], profiles: {}, stale: false, hub_cost: null, hub_cost_reason: null,
      results: null, settings: null }
    await page.route(u => /\/api\/campus-electrical\/[^/]+$/.test(u.pathname), r => r.fulfill({ json: state }))
    await page.route(u => /\/api\/campus-electrical\/[^/]+\/.+/.test(u.pathname), r => r.fulfill({ json: {} }))
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    await page.evaluate(async ({ url, name }) => {
      const { projectsApi } = await import(url)
      const list = await projectsApi.list().catch(() => [])
      if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
    }, { url: '/src/api/projects.ts', name: PROJECT })
    await page.reload(); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }

    const nav = async (d) => page.evaluate(async (d) => {
      const { applyUiNavigate } = await import('/src/components/uiNavigate.ts')
      applyUiNavigate(d)
    }, d)
    const ui = () => page.evaluate(async () => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      const s = useUIStore.getState()
      return { mode: s.uiMode, panel: s.activeSlidePanel, back: s.flowReturn?.panel ?? null, request: s.panelSectionRequest }
    })
    const heading = () => page.evaluate(() => {
      const el = document.querySelector('[data-panel-section="island"]')
      if (!el) return null
      const scroller = el.closest('.overflow-y-auto')
      const h = el.querySelector('h2')?.getBoundingClientRect()
      const s = scroller?.getBoundingClientRect()
      return { headingTop: h?.top, headingBottom: h?.bottom, scrollerTop: s?.top, visible: !!h && !!s && h.top >= s.top && h.bottom <= s.bottom }
    })

    await page.waitForTimeout(3000)  // let the app's own project switch settle: it counts as a manual navigation
    // (a/b) a jump from another view.
    await page.evaluate(async (m) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().setSlidePanel(m === 'guided' ? 'hubDesign' : 'overview')
    }, mode)
    await page.waitForTimeout(800)
    await nav({ kind: 'navigate', panel_id: 'campusStudy', panel_section: 'island' })
    await page.waitForTimeout(1500)
    const jumped = { ui: await ui(), heading: await heading() }
    facts.push({ mode, shot: 'jump', ...jumped })
    await page.screenshot({ path: path.join(OUT, `${mode}-jump-from-other-view.png`) })
    written.push(`${mode}-jump-from-other-view.png`)

    // (c) the panel already open, scrolled in place: no Back.
    await page.evaluate(async () => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.setState({ flowReturn: null })
      document.querySelector('[data-panel-section="island"]')?.closest('.overflow-y-auto')?.scrollTo(0, 0)
    })
    await page.waitForTimeout(300)
    await nav({ kind: 'navigate', panel_id: 'campusStudy', panel_section: 'island' })
    await page.waitForTimeout(1200)
    const inPlace = { ui: await ui(), heading: await heading() }
    facts.push({ mode, shot: 'in-place', ...inPlace })
    await page.screenshot({ path: path.join(OUT, `${mode}-scroll-in-place.png`) })
    written.push(`${mode}-scroll-in-place.png`)
    if (!jumped.heading?.visible) problems.push(`${mode}: island heading not fully visible after the jump`)
    if (!inPlace.heading?.visible) problems.push(`${mode}: island heading not fully visible after scrolling in place`)
    if (jumped.ui.mode !== mode || inPlace.ui.mode !== mode) problems.push(`${mode}: mode changed`)
    if (!jumped.ui.back) problems.push(`${mode}: no Back origin after the jump`)
    if (inPlace.ui.back) problems.push(`${mode}: Back set when scrolling in place`)
    await ctx.close()
  }
} finally { await browser.close() }
console.log(JSON.stringify(facts, null, 1))
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
