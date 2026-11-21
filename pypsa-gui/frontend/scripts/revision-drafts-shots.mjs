// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * SH-07d PR B: screenshot the run card's report revision drafts in every
 * draft state, light and dark, Guided and Expert (UX (26) condition 7).
 *
 * Needs the API on :8000 and Vite on :5173 (BASE overrides), signed in as the
 * dev admin (`dev-credentials.mjs`). Only `/api/chat/runs/*`,
 * `/api/chat/activity` and the run report's comments and document are mocked,
 * from `DRAFT_STATES` in `src/components/runMonitor/runFixtures.ts`. No model
 * is called and nothing is written.
 *
 * Usage: node scripts/revision-drafts-shots.mjs [outDir]
 * Writes `<mode>-<theme>-<state>.png` for every DRAFT_STATES key.
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'study-harness', 'evidence', '07d'))
const PROJECT = 'Run monitor shots'
const FIXTURES = '/src/components/runMonitor/runFixtures.ts'
const STORES = { chat: '/src/store/chatStore.ts', ui: '/src/store/uiStore.ts', projects: '/src/api/projects.ts' }

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
const written = []

try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
  const credentials = devCredentials()
  await login.locator('#email').fill(credentials.email)
  await login.locator('#password').fill(credentials.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  await login.evaluate(async ({ url, name }) => {
    const { projectsApi } = await import(url)
    const list = await projectsApi.list().catch(() => [])
    if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
  }, { url: STORES.projects, name: PROJECT })
  const fx = await login.evaluate(async url => {
    const m = await import(url)
    return JSON.parse(JSON.stringify({ STATES: m.DRAFT_STATES, TASK_ID: m.TASK_ID, RID: m.DRAFT_REPORT_ID,
      COMMENTS: m.DRAFT_COMMENTS, DOC: m.DRAFT_DOC }))
  }, FIXTURES)
  const authenticated = await owner.storageState()
  await owner.close()

  const runs = [['guided', 'light'], ['guided', 'dark'], ['expert', 'light'], ['expert', 'dark']]
  for (const [mode, theme] of runs) {
    const context = await browser.newContext({ storageState: authenticated, viewport: { width: 1440, height: 1300 }, reducedMotion: 'reduce' })
    await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
    await context.addInitScript(({ mode, theme, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
      localStorage.setItem('network-diagram:theme-schema', '2')
      localStorage.setItem('network-diagram:assistant-dock-width', '520')
      localStorage.setItem('network-diagram:assistant-dock-pinned', 'pinned')
    }, { mode, theme, project: PROJECT })
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => problems.push(`${mode}-${theme}: page error: ${error.message}`))

    let state = 'drafts_none'
    await page.route(url => url.pathname.startsWith('/api/chat/runs/'), route => route.fulfill({ json: fx.STATES[state] }))
    await page.route(url => url.pathname === '/api/chat/activity', route => route.fulfill({ json: {
      version: 1, project_id: PROJECT, filter: 'all', cursor: 'c:1', revision: 1, resnapshot_required: false,
      next_page: null, total: 0, warnings: [], scheduling_available: false, items: [] } }))
    await page.route(url => url.pathname.endsWith(`/reports/${fx.RID}/comments`), route => route.fulfill({
      // A confirmed draft addressed its comment: it leaves the open list.
      json: state === 'drafts_confirmed'
        ? { ...fx.COMMENTS, comments: fx.COMMENTS.comments.map(c => (c.comment_id === 'c1' ? { ...c, status: 'addressed' } : c)) }
        : fx.COMMENTS }))
    await page.route(url => url.pathname.endsWith(`/reports/${fx.RID}`), route => route.fulfill({ json: fx.DOC }))

    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    const openProject = page.getByRole('link', { name: /Resume project/ })
    if (await openProject.count()) {
      await openProject.first().click()
      await page.waitForURL(url => !url.pathname.startsWith('/projects'))
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(1500)
    }
    for (const key of Object.keys(fx.STATES)) {
      try {
        state = key
        await page.evaluate(async ({ stores, taskId }) => {
          const [{ useChatStore }, { useUIStore }] = await Promise.all([import(stores.chat), import(stores.ui)])
          useUIStore.getState().setAssistantDockOpen(true)
          useChatStore.setState({ pending: null, choice: null, messages: [] })
          await new Promise(r => setTimeout(r, 50))
          useChatStore.setState({ messages: [{ id: `shot-${Date.now()}`, ts: Date.now(), role: 'tool', content: '',
            tool_use_id: `shot-${taskId}`, tool_name: 'start_task', tool_result: { task_id: taskId, title: 'Own site study',
              status: 'completed', run: { stage: 'handoff' } } }] })
        }, { stores: STORES, taskId: fx.TASK_ID })
        const block = page.locator('[data-testid="revision-drafts"]').first()
        await block.waitFor()
        await page.locator('[data-testid="revision-comment"]').first().waitFor()
        await page.waitForTimeout(300)
        await block.scrollIntoViewIfNeeded()
        const file = path.join(OUT, `${mode}-${theme}-${key}.png`)
        await page.locator('[data-testid="run-monitor"]').first().screenshot({ path: file })
        written.push(path.basename(file))
      } catch (error) {
        problems.push(`${mode}-${theme}-${key}: ${error.message.split('\n')[0]}`)
      }
    }
    await context.close()
  }
} finally {
  await browser.close()
}
console.log(`wrote ${written.length} shots to ${OUT}`)
if (problems.length) {
  console.error(problems.join('\n'))
  process.exit(1)
}
