// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Study harness issue 08: screenshot every run state of the plan card and the
 * Envelope section, in Guided and in Expert, for the PR (UX conditions 1 and 8).
 *
 * Needs the API on :8000 and Vite on :5173 (BASE overrides), signed in as the
 * dev admin (`dev-credentials.mjs`). Only `/api/chat/runs/*` and
 * `/api/chat/activity` are mocked, from `src/components/runMonitor/runFixtures.ts`
 * (read through Vite, so the shots and the tests share one source). The
 * fixture run is put in the companion as a local turn, the way "Open the run ›"
 * does; no model is called.
 *
 * Usage: node scripts/run-monitor-shots.mjs [outDir]
 * Writes `<mode>-<state>.png` for every STATES key, plus
 * `<mode>-activity-row.png`, `<mode>-activity-open-run.png` and
 * `<mode>-link-landing.png`.
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'study-harness', 'evidence', '08'))
const PROJECT = 'Run monitor shots'
const FIXTURES = '/src/components/runMonitor/runFixtures.ts'
const STORES = { chat: '/src/store/chatStore.ts', ui: '/src/store/uiStore.ts', projects: '/src/api/projects.ts' }

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
const written = []

/** The plan-review Choice card the envelope states render in. */
const REVIEW = {
  tool_use_id: 'tu_review', intent: 'plan_review', title: 'Own site study', question: 'Approve this plan?',
  detail: 'Brief · Set up · Inputs · Check inputs · Solve · Check results · Explore options · Report · Hand-off',
  options: [{ label: 'Approve' }, { label: 'Change' }], allow_free_text: true,
}

try {
  // Real sign-in, as smoke-chat-composer does.
  const owner = await browser.newContext()
  const login = await owner.newPage()
  await login.goto(BASE)
  const credentials = devCredentials()
  await login.locator('#email').fill(credentials.email)
  await login.locator('#password').fill(credentials.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  // A project to open the workspace on (saved once; reused after).
  await login.evaluate(async ({ url, name }) => {
    const { projectsApi } = await import(url)
    const list = await projectsApi.list().catch(() => [])
    if (!list.some(p => p.name === name)) await projectsApi.save(name, false, true, undefined, true)
  }, { url: STORES.projects, name: PROJECT })
  const fx = await login.evaluate(async url => {
    const m = await import(url)
    return JSON.parse(JSON.stringify({ STATES: m.STATES, STEPS: m.STATE_STEPS, TASK_ID: m.TASK_ID, ENVELOPE: m.ENVELOPE }))
  }, FIXTURES)
  const authenticated = await owner.storageState()
  await owner.close()

  for (const mode of ['guided', 'expert']) {
    const context = await browser.newContext({ storageState: authenticated, viewport: { width: 1440, height: 1300 }, reducedMotion: 'reduce' })
    await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
    await context.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:assistant-dock-width', '520')
      localStorage.setItem('network-diagram:assistant-dock-pinned', 'pinned')
    }, { mode, project: PROJECT })
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => problems.push(`${mode}: page error: ${error.message}`))

    // The run's server, from the fixtures. `state` is the one on screen.
    let state = 'gate_pass'
    await page.route(url => url.pathname.startsWith('/api/chat/runs/'), route => {
      const action = new URL(route.request().url()).pathname.split('/').pop()
      if (action === 'present') return route.fulfill({ json: {} })
      const next = action === 'stop' ? 'stopped' : action === 'pause' ? 'paused' : action === 'resume' ? 'gate_pass' : state
      if (action !== 'view') state = next
      return route.fulfill({ json: fx.STATES[next] })
    })
    await page.route(url => url.pathname === '/api/chat/activity', route => route.fulfill({ json: {
      version: 1, project_id: PROJECT, filter: 'all', cursor: 'c:1', revision: 1, resnapshot_required: false,
      next_page: null, total: 1, warnings: [], scheduling_available: false,
      items: [{ id: `run:${fx.TASK_ID}`, source_id: fx.TASK_ID, project_id: PROJECT, family: 'run', title: 'Own site study',
        native_status: 'needs_user', status: 'needs_review', needs_you: true, evidence_available: false, tool_use_id: fx.TASK_ID }],
    } }))

    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    // Open the workspace on the project (the sign-in lands on the project list).
    const openProject = page.getByRole('link', { name: /Resume project/ })
    if (await openProject.count()) {
      await openProject.first().click()
      await page.waitForURL(url => !url.pathname.startsWith('/projects'))
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(1500)
    }
    const shoot = async (name, target = page.locator('[data-testid="assistant-dock"]')) => {
      const file = path.join(OUT, `${mode}-${name}.png`)
      await target.screenshot({ path: file })
      written.push(path.basename(file))
    }
    /** Put the run in the companion as a local turn, with the plan review for an envelope state. */
    const showRun = async (key) => {
      state = key
      const envelope = key.startsWith('envelope_')
      await page.evaluate(async ({ stores, taskId, envelope, review, proposed }) => {
        const [{ useChatStore }, { useUIStore }] = await Promise.all([import(stores.chat), import(stores.ui)])
        useUIStore.getState().setAssistantDockOpen(true)
        useChatStore.setState({ pending: null, choice: null, messages: [] })
        await new Promise(r => setTimeout(r, 50))
        useChatStore.setState({
          messages: [{ id: `shot-${Date.now()}`, ts: Date.now(), role: 'tool', content: '', tool_use_id: `shot-${taskId}`,
            tool_name: 'start_task', tool_result: { task_id: taskId, title: 'Own site study', status: 'ready',
              run: { stage: 'intake' }, ...(envelope ? { envelope: proposed } : {}) } }],
          choice: envelope ? review : null,
        })
      }, { stores: STORES, taskId: fx.TASK_ID, envelope, review: REVIEW, proposed: fx.ENVELOPE })
      const shown = page.locator(envelope ? '[data-testid="envelope-section"]' : '[data-testid="run-monitor"]').first()
      await shown.waitFor()
      await page.waitForTimeout(300)
      // The plan review sits under the run's card: bring the card the state is about to the top.
      await shown.evaluate(e => (e.closest('[data-testid^="chat-choice"]') ?? e).scrollIntoView({ block: 'start' }))
    }

    for (const key of Object.keys(fx.STATES)) {
      try {
        await showRun(key)
        const step = fx.STEPS[key]
        if (step?.untickEdits) for (const i of step.untickEdits) await page.getByTestId('envelope-edit').nth(i).locator('input').click()
        if (step?.solves) await page.getByTestId('envelope-solves').fill(String(step.solves))
        if (step?.click) await page.getByTestId(step.click).click()
        if (key === 'stop_confirm') await page.getByTestId('run-stop-confirm').waitFor()
        if (key === 'envelope_unattended_disabled') await page.getByTestId('envelope-unattended-reason').waitFor()
        if (key === 'envelope_email_disabled') await page.getByTestId('envelope-email-reason').waitFor()
        await shoot(key)
      } catch (error) {
        problems.push(`${mode}-${key}: ${error.message.split('\n')[0]}`)
      }
    }

    // A › link from the plan card, and where it lands (Guided: in place over its home).
    try {
      await showRun('solver_error')
      await page.getByRole('button', { name: 'Open the solve log ›' }).click()
      await page.waitForTimeout(800)
      await shoot('link-landing', page)
    } catch (error) {
      problems.push(`${mode}-link-landing: ${error.message.split('\n')[0]}`)
    }

    // The Activity row, and where "Open the run ›" lands.
    try {
      await page.evaluate(async ({ stores }) => {
        const [{ useChatStore }, { useUIStore }] = await Promise.all([import(stores.chat), import(stores.ui)])
        useUIStore.setState({ fieldDrawer: null, activeSlidePanel: null, flowReturn: null })
        useUIStore.getState().setAssistantDockOpen(true)
        useChatStore.setState({ pending: null, choice: null, messages: [] })
      }, { stores: STORES })
      state = 'gate_fail'
      await page.locator('[data-testid="assistant-dock"]').getByRole('button', { name: 'Activity', exact: true }).click()
      await page.getByTestId('activity-open-run').waitFor()
      await shoot('activity-row')
      await page.getByTestId('activity-open-run').click()
      await page.locator('[data-testid="run-state"]').first().waitFor()
      await page.waitForTimeout(500)
      await shoot('activity-open-run')
    } catch (error) {
      problems.push(`${mode}-activity: ${error.message.split('\n')[0]}`)
    }
    await context.close()
  }
} finally {
  await browser.close()
}

console.log(`${written.length} screenshots in ${OUT}`)
for (const p of problems) console.log(`PROBLEM ${p}`)
process.exitCode = problems.length ? 1 : 0
