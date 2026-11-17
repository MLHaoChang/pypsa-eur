// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Study harness 14 S-C (#1590): screenshot the plan card's PPA price block in
 * its three states, in Guided and in Expert: priced, not evaluated (no target
 * set) and out of date at hand-off.
 *
 * Needs the API on :8000 and Vite on :5173 (BASE overrides), signed in as the
 * dev admin (`dev-credentials.mjs`). Only `/api/chat/runs/*` is mocked: the run
 * view comes from `src/components/runMonitor/runFixtures.ts` and the PPA block
 * from `ppaPrice.fixture.json`, which tests/test_run_ppa_price.py pins to
 * `harness.run_ppa.monitor`'s own output. No model is called.
 *
 * Usage: node scripts/sh14-ppa-shots.mjs [outDir]
 * Writes `<state>-<mode>.png` for each state and mode.
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'study-harness', 'shots', '14-sc'))
const PROJECT = 'Run monitor shots'
const FIXTURES = '/src/components/runMonitor/runFixtures.ts'
const PPA = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'components', 'runMonitor', 'ppaPrice.fixture.json'), 'utf8'))
const STORES = { chat: '/src/store/chatStore.ts', ui: '/src/store/uiStore.ts', projects: '/src/api/projects.ts' }

await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
const problems = []
const written = []

/** The run as `/api/chat/runs/{id}/view` serves it in each state. */
function views(fx) {
  const done = fx.STATES.finished
  const withPpa = (v, key) => ({ ...v, title: 'IPP hybrid study', run: { ...v.run, profile: 'eh_ipp_hybrid@3' },
    monitor: { ...v.monitor, ppa: PPA[key] } })
  // Out of date at hand-off: the read-back's rule fails, the run waits for the user.
  const stages = done.run.stages.map(s => s.stage === 'handoff'
    ? { stage: 'handoff', status: 'failed', gate: [{ rule: 'ho.kpis_read', verdict: 'fail', fact: 'handoff.kpis_read', value: false }] }
    : s)
  const stale = { ...done, status: 'needs_review', run: { ...done.run, status: 'needs_user', stage: 'handoff', stages },
    monitor: { ...done.monitor, actions: ['resume', 'stop'] } }
  return { priced: withPpa(done, 'priced'), 'no-target': withPpa(done, 'not_evaluated'), 'out-of-date': withPpa(stale, 'out_of_date') }
}

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
    return JSON.parse(JSON.stringify({ STATES: m.STATES, TASK_ID: m.TASK_ID }))
  }, FIXTURES)
  const VIEWS = views(fx)
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
    let state = 'priced'
    await page.route(url => url.pathname.startsWith('/api/chat/runs/'), route => {
      const action = new URL(route.request().url()).pathname.split('/').pop()
      if (action === 'present') return route.fulfill({ json: {} })
      return route.fulfill({ json: VIEWS[state] })
    })
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    const openProject = page.getByRole('link', { name: /Resume project/ })
    if (await openProject.count()) {
      await openProject.first().click()
      await page.waitForURL(url => !url.pathname.startsWith('/projects'))
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(1500)
    }
    for (const key of Object.keys(VIEWS)) {
      try {
        state = key
        await page.evaluate(async ({ stores, taskId }) => {
          const [{ useChatStore }, { useUIStore }] = await Promise.all([import(stores.chat), import(stores.ui)])
          useUIStore.getState().setAssistantDockOpen(true)
          useChatStore.setState({ pending: null, choice: null, messages: [] })
          await new Promise(r => setTimeout(r, 50))
          useChatStore.setState({ messages: [{ id: `shot-${Date.now()}`, ts: Date.now(), role: 'tool', content: '',
            tool_use_id: `shot-${taskId}`, tool_name: 'start_task',
            tool_result: { task_id: taskId, title: 'IPP hybrid study', status: 'ready', run: { stage: 'solve' } } }] })
        }, { stores: STORES, taskId: fx.TASK_ID })
        const block = page.locator('[data-testid="run-ppa"]').first()
        await block.waitFor()
        const shown = await block.getAttribute('data-state')
        const want = { priced: 'priced', 'no-target': 'not_evaluated', 'out-of-date': 'out_of_date' }[key]
        if (shown !== want) problems.push(`${key}-${mode}: shows ${shown}, not ${want}`)
        await page.waitForTimeout(300)
        const card = page.locator('[data-testid="run-monitor"]').first()
        await card.scrollIntoViewIfNeeded()
        const file = path.join(OUT, `${key}-${mode}.png`)
        await card.screenshot({ path: file })
        written.push(path.basename(file))
      } catch (error) {
        problems.push(`${key}-${mode}: ${error.message.split('\n')[0]}`)
      }
    }
    await context.close()
  }
} finally {
  await browser.close()
}

console.log(`${written.length} screenshots in ${OUT}`)
for (const p of problems) console.log(`PROBLEM ${p}`)
process.exitCode = problems.length ? 1 : 0
