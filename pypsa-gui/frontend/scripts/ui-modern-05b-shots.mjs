// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * ui-modern 05 slice 2 screenshot harness: Guided sections 2 (Carriers), 4 (Loads), 5 (Assets),
 * 6 (Time series) and 7 (Commercial) on the real app, for the Data Center Energy Hub (`dc-`) and
 * the Island Microgrid (`mg-`), each in dark and light: `<tag>-<project>-<theme>-s0<n>.jpg`.
 * With `after`, once in dark, it also takes: a folded group and section 3 on the staged microgrid,
 * the Back pill after a carrier's "Edit", the Time Series drawer opened from a row, the empty
 * state (the generation-only hybrid's Loads) and Expert Economics on the microgrid.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs).
 * It creates the template projects it needs when they are missing.
 * Usage: node scripts/ui-modern-05b-shots.mjs <before|after> [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const TAG = process.argv[2] === 'before' ? 'before' : 'after'
const OUT = path.resolve(process.argv[3] || path.join(here, '..', '..', '..', '.scratch', 'ui-modern', 'shots-05b'))
const TEMPLATES = [['Data Center Energy Hub', 'eh_datacenter'], ['Island Microgrid', 'eh_microgrid'],
  ['Staged Island Microgrid', 'eh_microgrid_staged'], ['Hybrid IPP', 'eh_ipp_hybrid']]
const PROJECTS = [['dc', 'Data Center Energy Hub'], ['mg', 'Island Microgrid']]
const SECTIONS = [['s02', 'carriers'], ['s04', 'loads'], ['s05', 'assets'], ['s06', 'profiles'], ['s07', 'commercial']]
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []

async function open(auth, project, theme) {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' })
  await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await ctx.addInitScript(({ project, theme }) => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', 'guided')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:theme', theme)
  }, { project, theme })
  const page = await ctx.newPage()
  page.setDefaultTimeout(30000)
  page.on('pageerror', e => problems.push(`${project}-${theme}: page error: ${e.message}`))
  await page.goto(`${BASE}/`)
  await page.waitForLoadState('networkidle')
  // The backend holds one live network: switch it to this project before the app reads it.
  await page.evaluate(async ({ url, name }) => {
    const { projectsApi } = await import(url)
    await projectsApi.activate(name)
  }, { url: '/src/api/projects.ts', name: project })
  await page.reload(); await page.waitForLoadState('networkidle')
  // A fresh API has no open project: the app lands on the projects page first.
  if (new URL(page.url()).pathname.startsWith('/projects')) {
    await page.getByText('Resume project').first().click()
    await page.waitForURL(u => !u.pathname.startsWith('/projects'))
    await page.waitForLoadState('networkidle')
  }
  await page.waitForTimeout(3000)   // the app's own project switch settles first
  return { ctx, page }
}

async function show(page, section) {
  const view = page.getByTestId(`section-view-${section}`)
  // A project that has just opened moves to its first open section once; ask again if it did.
  for (let attempt = 0; ; attempt++) {
    await page.evaluate(async (section) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      useUIStore.getState().setUiMode('guided', { explicit: true })
      useUIStore.getState().setFieldDrawer(null)
      useUIStore.getState().setSlidePanel('hubDesign')
      useUIStore.getState().setActiveSection(section)
    }, section)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(1500)
    if (await view.isVisible()) break
    if (attempt === 4) throw new Error(`section ${section} did not open`)
  }
  return view
}

/** A long section scrolls inside the panel: grow the window to its height so the shot holds all of it. */
async function shootView(page, view, file) {
  const tall = await view.evaluate(el => el.scrollHeight)
  await page.setViewportSize({ width: 1280, height: Math.max(900, tall + 320) })
  await page.waitForTimeout(300)
  await view.screenshot({ path: path.join(OUT, file), type: 'jpeg', quality: 80 })
  await page.setViewportSize({ width: 1280, height: 900 })
}

const shootPage = (page, file) => page.screenshot({ path: path.join(OUT, file), type: 'jpeg', quality: 80 })

try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
  const c = devCredentials()
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  await login.evaluate(async (templates) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const have = (await projectsApi.list()).map(p => p.name ?? p)
    for (const [name, tid] of templates) if (!have.includes(name)) await projectsApi.createFromTemplate(tid, name)
  }, TEMPLATES)
  const auth = await owner.storageState()
  await owner.close()

  for (const [short, project] of PROJECTS) {
    for (const theme of ['dark', 'light']) {
      const { ctx, page } = await open(auth, project, theme)
      for (const [n, section] of SECTIONS) {
        const view = await show(page, section)
        await shootView(page, view, `${TAG}-${short}-${theme}-${n}.jpg`)
      }
      if (TAG === 'after' && theme === 'dark' && short === 'dc') {
        // The Time Series drawer, opened from a row of section 6.
        await show(page, 'profiles')
        await page.getByTestId('section-held-edit').first().click()
        await page.getByTestId('field-drawer').waitFor()
        await page.waitForLoadState('networkidle'); await page.waitForTimeout(1200)
        await shootPage(page, 'after-dc-dark-s06-edit-drawer.jpg')
        // An asset's own editor over the card.
        await show(page, 'assets')
        await page.getByTestId('section-held-edit').first().click()
        await page.getByTestId('field-drawer').waitFor()
        await page.waitForTimeout(1200)
        await shootPage(page, 'after-dc-dark-s05-edit-drawer.jpg')
        // A carrier has no Guided editor: Expert, with the Back pill.
        await show(page, 'carriers')
        await page.getByTestId('section-held-edit').first().click()
        await page.getByRole('button', { name: /Back to/ }).waitFor()
        await page.waitForTimeout(1200)
        await shootPage(page, 'after-dc-dark-s02-edit-back-pill.jpg')
      }
      if (TAG === 'after' && theme === 'dark' && short === 'mg') {
        // The same contracts and finance-inputs editors in Expert Economics.
        await page.evaluate(async () => {
          const { useUIStore } = await import('/src/store/uiStore.ts')
          useUIStore.getState().setUiMode('expert', { explicit: true })
          useUIStore.getState().setSlidePanel('economics')
        })
        await page.getByRole('tablist', { name: 'Economics' }).getByRole('tab', { name: 'Tariffs & contracts' }).click()
        await page.waitForLoadState('networkidle'); await page.waitForTimeout(1200)
        await page.screenshot({ path: path.join(OUT, 'after-mg-dark-expert-economics.jpg'), type: 'jpeg', quality: 80, fullPage: true })
      }
      await ctx.close()
    }
  }
  if (TAG === 'after') {
    const staged = await open(auth, 'Staged Island Microgrid', 'dark')
    await shootView(staged.page, await show(staged.page, 'assets'), 'after-mgs-dark-s05-folded.jpg')
    await shootView(staged.page, await show(staged.page, 'connection'), 'after-mgs-dark-s03.jpg')
    await staged.ctx.close()
    const ipp = await open(auth, 'Hybrid IPP', 'dark')
    await shootView(ipp.page, await show(ipp.page, 'loads'), 'after-ipp-dark-s04-empty.jpg')
    await ipp.ctx.close()
  }
} finally {
  await browser.close()
}
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log(`shots in ${OUT}`)
