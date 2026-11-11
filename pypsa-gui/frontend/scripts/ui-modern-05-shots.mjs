// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * ui-modern 05 screenshot harness: Guided sections 3 (Connection & grid), 5 (Assets & sizing) and
 * 7 (Commercial & parties) on the real app, for the Data Center Energy Hub (`dc-`) and the Island
 * Microgrid (`mg-`), each in dark and light. One file per section view:
 * `<tag>-<project>-<theme>-s0<n>.jpg`, where <tag> is `before` or `after`.
 *
 * Needs the API on :8000 and Vite on :5173, signed in as the dev admin (scripts/dev-credentials.mjs),
 * and both projects created from their templates (eh_datacenter, eh_microgrid).
 * Usage: node scripts/ui-modern-05-shots.mjs <before|after> [outDir]      env: BASE
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
const OUT = path.resolve(process.argv[3] || path.join(here, '..', '..', '..', '.scratch', 'ui-modern', 'shots-05'))
const PROJECTS = [['dc', 'Data Center Energy Hub'], ['mg', 'Island Microgrid']]
const SECTIONS = [['s03', 'connection'], ['s05', 'assets'], ['s07', 'commercial']]
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
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

  for (const [short, project] of PROJECTS) {
    for (const theme of ['dark', 'light']) {
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
      page.on('pageerror', e => problems.push(`${short}-${theme}: page error: ${e.message}`))
      await page.goto(`${BASE}/`)
      await page.waitForLoadState('networkidle')
      // The backend holds one live network: switch it to this project before the app reads it.
      await page.evaluate(async ({ url, name }) => {
        const { projectsApi } = await import(url)
        await projectsApi.activate(name)
      }, { url: '/src/api/projects.ts', name: project })
      await page.reload(); await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) {
        await resume.first().click()
        await page.waitForURL(u => !u.pathname.startsWith('/projects'))
        await page.waitForLoadState('networkidle')
      }
      await page.waitForTimeout(3000)   // the app's own project switch settles first
      for (const [n, section] of SECTIONS) {
        await page.evaluate(async (section) => {
          const { useUIStore } = await import('/src/store/uiStore.ts')
          useUIStore.getState().setSlidePanel('hubDesign')
          useUIStore.getState().setActiveSection(section)
        }, section)
        const view = page.getByTestId(`section-view-${section}`)
        await view.waitFor()
        await page.waitForLoadState('networkidle')
        await page.waitForTimeout(800)
        const name = await page.evaluate(async () => {
          const { useUIStore } = await import('/src/store/uiStore.ts')
          return useUIStore.getState().currentProject
        })
        if (name !== project) problems.push(`${short}-${theme}-${n}: the open project is ${name}`)
        // A long section scrolls inside the panel: grow the window to its height so the shot holds all of it.
        const tall = await view.evaluate(el => el.scrollHeight)
        await page.setViewportSize({ width: 1280, height: Math.max(900, tall + 320) })
        await page.waitForTimeout(300)
        await view.screenshot({ path: path.join(OUT, `${TAG}-${short}-${theme}-${n}.jpg`), type: 'jpeg', quality: 80 })
        await page.setViewportSize({ width: 1280, height: 900 })
      }
      // Expert, once (ui-modern 05, UX U7): the same tariff builder in Economics, and the Issues list,
      // where an assumed row shows its whole sentence.
      if (TAG === 'after' && theme === 'dark') {
        for (const [n, panel, wait] of [['expert-economics', 'economics', 'tariff-builder'], ['expert-issues', 'issues', null]]) {
          if (panel === 'economics' && short !== 'dc') continue
          await page.evaluate(async (panel) => {
            const { useUIStore } = await import('/src/store/uiStore.ts')
            useUIStore.getState().setUiMode('expert', { explicit: true })
            useUIStore.getState().setSlidePanel(panel)
          }, panel)
          if (panel === 'economics') await page.getByRole('tablist', { name: 'Economics' }).getByRole('tab', { name: 'Tariffs & contracts' }).click()
          if (wait) await page.getByTestId(wait).first().waitFor()
          await page.waitForLoadState('networkidle')
          await page.waitForTimeout(1200)
          await page.screenshot({ path: path.join(OUT, `${TAG}-${short}-${theme}-${n}.jpg`), type: 'jpeg', quality: 80 })
        }
      }
      await ctx.close()
    }
  }
} finally {
  await browser.close()
}
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
console.log(`shots in ${OUT}`)
