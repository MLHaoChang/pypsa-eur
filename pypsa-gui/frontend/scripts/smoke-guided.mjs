#!/usr/bin/env node
/**
 * Browser smoke for the guided-mode phases (spec
 * docs/superpowers/specs/2026-09-27-guided-mode.md §8.4; path per phase).
 *
 *   cd pypsa-gui/frontend
 *   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs \
 *     --phase P22.9 [--template eh_datacenter] [--out <dir>] [--keep]
 *
 * It starts its own uvicorn (local mode, ANTHROPIC_API_KEY unset, app data
 * and projects under a scratch dir), Vite on 5173 and — after the send-gate
 * check — the OpenAI-wire stub model, then walks the phase path in a fresh
 * browser context, asserting on test ids. Every process it starts is stopped
 * on exit. Screenshots (and, on failure, the page console) go to --out.
 *
 * Playwright is not a dependency of this package: it is loaded by absolute
 * path (Node's ESM resolver ignores NODE_PATH), per spec §8.4.
 *
 * Exit codes: 0 pass · 1 app failure · 2 usage · 3 tooling failure (the
 * browser self-check, a busy port or a server that never came up).
 */
import { createRequire } from 'node:module'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers'
const PLAYWRIGHT = '/opt/node22/lib/node_modules/playwright'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const BACKEND = path.resolve(FRONTEND, '..', 'backend')
const REPO = path.resolve(FRONTEND, '..', '..')
const PYTHON = process.env.SMOKE_PYTHON
  ?? (fs.existsSync('/tmp/claude-0/venv/bin/python') ? '/tmp/claude-0/venv/bin/python' : 'python3')

const API = 'http://127.0.0.1:8000'
const WEB = 'http://127.0.0.1:5173'
const STUB_PORT = 11999
const STUB_PROFILE = 'smoke-stub'

const TEMPLATE_NAMES = {
  eh_datacenter: 'Data Center Energy Hub',
  eh_h2_hub: 'Industrial Hydrogen Hub',
  eh_microgrid: 'Island Microgrid',
}
const PHASES = new Set(['P22.9'])

// ── args ────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = { phase: null, template: 'eh_datacenter', keep: false,
    out: path.join(os.tmpdir(), 'pypsa-gui-smoke') }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--phase') out.phase = argv[++i]
    else if (a === '--template') out.template = argv[++i]
    else if (a === '--out') out.out = argv[++i]
    else if (a === '--keep') out.keep = true
    else { console.error(`unknown argument ${a}`); process.exit(2) }
  }
  if (!PHASES.has(out.phase)) {
    console.error(`--phase must be one of ${[...PHASES].join(', ')} (later phases extend this script)`)
    process.exit(2)
  }
  if (!TEMPLATE_NAMES[out.template]) {
    console.error(`--template must be one of ${Object.keys(TEMPLATE_NAMES).join(', ')}`)
    process.exit(2)
  }
  return out
}
const args = parseArgs(process.argv.slice(2))
fs.mkdirSync(args.out, { recursive: true })
const RUN = path.join(args.out, `run-${new Date().toISOString().replace(/[:.]/g, '-')}`)
fs.mkdirSync(RUN, { recursive: true })

// ── reporting ───────────────────────────────────────────────────────────────
class ToolingError extends Error {}
const t0 = Date.now()
const secs = () => ((Date.now() - t0) / 1000).toFixed(1).padStart(6)
let stepNo = 0
function step(title) { console.log(`[${secs()}s] ${String(++stepNo).padStart(2, '0')} ${title}`) }
function ok(msg) { console.log(`           ok  ${msg}`) }
function info(msg) { console.log(`           ..  ${msg}`) }
function check(cond, msg) {
  if (!cond) throw new Error(`assertion failed: ${msg}`)
  ok(msg)
}
const shots = []
async function shot(page, name) {
  const file = path.join(args.out, `${String(shots.length + 1).padStart(2, '0')}-${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  shots.push(file)
  info(`screenshot ${file}`)
}

// ── processes ───────────────────────────────────────────────────────────────
const procs = []
function start(name, cmd, argv, opts) {
  const log = fs.openSync(path.join(RUN, `${name}.log`), 'a')
  const p = spawn(cmd, argv, { ...opts, detached: true, stdio: ['ignore', log, log] })
  procs.push({ name, p })
  info(`started ${name} (pid ${p.pid}), log ${path.join(RUN, `${name}.log`)}`)
  return p
}
async function stopAll() {
  for (const { name, p } of procs.reverse()) {
    if (p.exitCode !== null || p.signalCode !== null) continue
    try { process.kill(-p.pid, 'SIGTERM') } catch { /* gone */ }
    for (let i = 0; i < 40 && p.exitCode === null && p.signalCode === null; i++) await sleep(100)
    if (p.exitCode === null && p.signalCode === null) {
      try { process.kill(-p.pid, 'SIGKILL') } catch { /* gone */ }
    }
    console.log(`           stopped ${name}`)
  }
}
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { void stopAll().then(() => process.exit(130)) })
}

async function portFree(port) {
  try {
    await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) })
    return false
  } catch (e) {
    return e?.cause?.code === 'ECONNREFUSED'
  }
}
async function waitFor(url, what, timeoutMs, accept = r => r.ok) {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(3000) })
      if (accept(r)) return r
    } catch { /* not up yet */ }
    await sleep(500)
  }
  throw new ToolingError(`${what} did not answer ${url} within ${timeoutMs / 1000}s`)
}

// ── HTTP helpers ────────────────────────────────────────────────────────────
async function api(method, route, body) {
  const r = await fetch(`${API}${route}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`${method} ${route} → ${r.status}: ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : null
}

const TABLES = ['buses', 'links', 'generators']
async function snapshotTables() {
  const snap = {}
  for (const t of TABLES) snap[t] = await api('GET', `/api/network/${t}`)
  return snap
}
/** Whole-row differences, `*_nom_opt` excluded (spec §2.1 accepted deviation). */
function tableDiffs(before, after) {
  const diffs = []
  let nomOpt = 0
  for (const t of TABLES) {
    const a = new Map(before[t].map(r => [r.name, r]))
    const b = new Map(after[t].map(r => [r.name, r]))
    for (const name of new Set([...a.keys(), ...b.keys()])) {
      const x = a.get(name), y = b.get(name)
      if (!x || !y) { diffs.push(`${t}/${name}: ${x ? 'removed' : 'added'}`); continue }
      for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) {
        if (JSON.stringify(x[k]) === JSON.stringify(y[k])) continue
        if (/_nom_opt$/.test(k)) { nomOpt++; continue }
        diffs.push(`${t}/${name}.${k}: ${JSON.stringify(x[k])} → ${JSON.stringify(y[k])}`)
      }
    }
  }
  return { diffs, nomOpt }
}

// ── the P22.9 path (spec §2.11) ─────────────────────────────────────────────
async function phaseP229(browser) {
  const consoleLines = []
  const requests = []
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.on('console', m => consoleLines.push(`[${m.type()}] ${m.text()}`))
  page.on('pageerror', e => consoleLines.push(`[pageerror] ${e.message}`))
  page.on('request', r => requests.push(r.url()))
  const byId = id => page.locator(`[data-testid="${id}"]`)
  // The sidebar's "Results" item (the header's overlay toggle shares the name).
  const resultsNav = () => page.getByRole('button', { name: 'Results', exact: true }).first()

  async function openDockInput() {
    const input = byId('chat-input')
    // The dock remembers its open state; wait for whichever is on screen.
    await page.waitForSelector(
      '[data-testid="chat-input"]:visible, [data-testid="assistant-dock-launcher"]:visible',
      { timeout: 30_000 })
    if (!(await input.isVisible().catch(() => false))) {
      await byId('assistant-dock-launcher').click()
    }
    await input.waitFor({ state: 'visible', timeout: 15_000 })
    await input.fill('hello')
  }

  try {
    // (a) the send gate, BEFORE any stub profile exists (spec §2.11, review B6)
    step('send gate with the default profile and no API key')
    const h0 = await api('GET', '/api/chat/health')
    info(`health: active_profile=${h0.active_profile?.id} chat_ready=${h0.chat_ready}`)
    check(h0.chat_ready === false, 'GET /api/chat/health reports chat_ready:false')
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await openDockInput()
    const send = byId('chat-send')
    await page.waitForFunction(
      () => document.querySelector('[data-testid="chat-send"]')?.disabled === true,
      null, { timeout: 15_000 })
    check(await send.isDisabled(), 'chat-send is disabled with text typed')
    check((await send.getAttribute('title')) === 'Add an API key first (Settings → Assistant)',
      'chat-send carries the "Add an API key first" hint')
    check(await byId('chat-send-gate').isVisible(), 'the key form is shown inline (chat-send-gate)')
    await shot(page, 'send-gate-disabled')

    // (b) only now: the stub model, as an auth:none profile, activated
    step('stub profile activated → Send enabled')
    start('stub', PYTHON, [path.join(BACKEND, 'smoke', 'stub_openai_endpoint.py')], { cwd: BACKEND })
    await waitFor(`http://127.0.0.1:${STUB_PORT}/v1/models`, 'stub model', 30_000)
    await api('PUT', `/api/chat/settings/llm/profiles/${STUB_PROFILE}`, {
      label: 'Smoke stub', preset: 'custom', wire: 'openai',
      base_url: `http://127.0.0.1:${STUB_PORT}/v1`, model: 'stub-model',
      tools: true, vision: false, auth: 'none', fallback_model: null, max_output_tokens: null,
    })
    await api('POST', '/api/chat/settings/llm/active', { profile_id: STUB_PROFILE })
    const h1 = await api('GET', '/api/chat/health')
    check(h1.chat_ready === true && h1.active_profile?.id === STUB_PROFILE,
      `health: active_profile=${h1.active_profile?.id} chat_ready=true`)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await openDockInput()
    await page.waitForFunction(
      () => document.querySelector('[data-testid="chat-send"]')?.disabled === false,
      null, { timeout: 15_000 })
    check(await byId('chat-send').isEnabled(), 'chat-send is enabled')
    check(!(await byId('chat-send-gate').isVisible().catch(() => false)), 'no inline key form')
    await byId('chat-input').fill('')
    await shot(page, 'send-enabled-stub')

    // (c) template from /projects → the workbench opens (obstacle 2)
    step(`template ${args.template} from /projects opens the workbench`)
    await page.getByRole('button', { name: /From template/ }).first().click()
    await byId('new-project-wizard').waitFor({ state: 'visible', timeout: 15_000 })
    await page.getByRole('button', { name: new RegExp(TEMPLATE_NAMES[args.template]) }).click()
    await page.waitForURL(/\/app\?project=/, { timeout: 60_000 })
    const project = new URL(page.url()).searchParams.get('project')
    check(Boolean(project), `navigated to /app?project=${project}`)
    await resultsNav().waitFor({ timeout: 30_000 })
    check(!(await byId('new-project-wizard').isVisible().catch(() => false)), 'wizard closed')
    check(!(await page.getByRole('button', { name: /^Resume/ }).isVisible().catch(() => false)),
      'no "Resume" step on the way in')
    await shot(page, 'workbench-opened')
    check(!requests.some(u => u.includes('/api/projects/unclaimed')),
      'local mode never requested /api/projects/unclaimed (bug 7)')

    const before = await snapshotTables()
    info(`live tables: ${TABLES.map(t => `${t}=${before[t].length}`).join(' ')}`)

    // Results → Adequacy → Energy Hub reference design → Run (recommended)
    step('Results → Adequacy → "Energy Hub reference design" → Run')
    await resultsNav().click()
    await page.getByRole('button', { name: 'Adequacy', exact: true }).click()
    const toggle = byId('eh-reference-design-toggle')
    await toggle.waitFor({ timeout: 30_000 })
    check((await toggle.textContent()).includes('Energy Hub reference design'),
      'section header reads "Energy Hub reference design"')
    if (!(await byId('eh-run').isVisible().catch(() => false))) await toggle.click()
    await byId('eh-template-banner').waitFor({ timeout: 30_000 })
    ok('template banner shown')
    await byId('eh-run').click()
    await byId('eh-readiness-paused').waitFor({ timeout: 30_000 })
    check(await byId('eh-template-banner').isVisible(), 'template banner stays while the study runs')
    await shot(page, 'study-running')

    step('study finishes → finished cue → report in view')
    const cue = byId('eh-study-finished-cue')
    await cue.waitFor({ state: 'visible', timeout: 10 * 60_000 })
    const study = await api('GET', '/api/results/eh_study')
    check(study?.status === 'done', `study status done (${study?.archetype})`)
    check((await cue.textContent()).trim() === 'Study finished — view report', 'cue text')
    await shot(page, 'finished-cue')
    await cue.click()
    await sleep(1200)  // smooth scroll
    const inView = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="eh-report"]')
      if (!el) return false
      const r = el.getBoundingClientRect()
      return r.top >= -2 && r.top < window.innerHeight
    })
    check(inView, 'eh-report scrolled into view')
    check(!(await cue.isVisible().catch(() => false)), 'cue hides after the click')
    await shot(page, 'report-in-view')

    step('verdict next step')
    const verdict = byId('eh-certification-verdict')
    const hasVerdict = await verdict.isVisible().catch(() => false)
    const v = hasVerdict ? await verdict.getAttribute('data-verdict') : null
    info(`certification verdict: ${v ?? '(none rendered)'}${hasVerdict ? ` — "${(await verdict.innerText()).trim()}"` : ''}`)
    if (v === 'pass') {
      check(!(await byId('eh-certification-next').isVisible().catch(() => false)),
        'pass → no next line')
    } else {
      const next = byId('eh-certification-next')
      await next.scrollIntoViewIfNeeded()
      const text = (await next.textContent()).trim()
      check(text.startsWith('Next: '), `next line: "${text}"`)
    }
    const okChip = page.locator('[data-testid^="eh-section-"][data-status="ok"]').first()
    if (await okChip.count()) {
      const cls = await okChip.getAttribute('class')
      check(cls.includes('text-success') && !cls.includes('text-accent'), '"ok" chip uses the success tone')
    }
    await shot(page, 'verdict-next')

    step('live tables after the study (only *_nom_opt may differ)')
    const afterStudy = tableDiffs(before, await snapshotTables())
    info(`*_nom_opt changes: ${afterStudy.nomOpt}`)
    check(afterStudy.diffs.length === 0,
      `buses/links/generators equal after the study${afterStudy.diffs.length ? `: ${afterStudy.diffs.slice(0, 5).join('; ')}` : ''}`)

    // FMEA sweep (bug 3's trigger)
    step('FMEA sweep → live tables equal')
    await page.getByRole('button', { name: 'FMEA', exact: true }).click()
    await byId('fmea-sweep').click()
    const until = Date.now() + 10 * 60_000
    let sweep = null
    await sleep(1000)
    while (Date.now() < until) {
      sweep = await api('GET', '/api/results/fmea_sweep')
      if (sweep && sweep.status !== 'running') break
      await sleep(1000)
    }
    check(sweep?.status === 'done', `sweep status done (base_restored=${sweep?.base_restored})`)
    await byId('fmea-table').waitFor({ timeout: 30_000 })
    await shot(page, 'fmea-after-sweep')
    const afterSweep = tableDiffs(before, await snapshotTables())
    info(`*_nom_opt changes: ${afterSweep.nomOpt}`)
    check(afterSweep.diffs.length === 0,
      `buses/links/generators equal after the sweep${afterSweep.diffs.length ? `: ${afterSweep.diffs.slice(0, 5).join('; ')}` : ''}`)
    const status = await api('GET', '/api/simulation/status')
    info(`simulation status after the sweep: dispatch=${status.dispatch} condition=${status.condition}`)
    // Bug 2: the sweep's completion re-reads the status the greeting shows,
    // so the dock must no longer say "Not solved yet." (nor claim "Solved —").
    const solve = byId('chat-launch-solve')
    await page.waitForFunction(() => {
      const t = document.querySelector('[data-testid="chat-launch-solve"]')?.textContent ?? ''
      return t !== '' && t !== 'Not solved yet.'
    }, null, { timeout: 15_000 }).catch(() => {})
    const greeting = ((await solve.textContent().catch(() => '')) ?? '').trim()
    info(`dock greeting: "${greeting}"`)
    check(greeting !== '' && greeting !== 'Not solved yet.', 'greeting is not "Not solved yet." after the sweep')
    if (status.dispatch === 'fresh' && status.condition == null) {
      check(greeting.startsWith('The network carries dispatch from a study re-solve'),
        'greeting names the study re-solve (no foreground solve recorded)')
    }
    await shot(page, 'greeting-after-sweep')

    // Tagging tour (obstacle 3)
    step('"How to tag the network" → Properties on a bus, Edit, coach mark on eh-bus-fields')
    await page.getByRole('button', { name: 'Adequacy', exact: true }).click()
    await toggle.waitFor({ timeout: 30_000 })
    if (!(await byId('eh-tagging-guide-button').isVisible().catch(() => false))) await toggle.click()
    await byId('eh-tagging-guide-button').click()
    const tour = page.locator('[data-testid="guide-tour"][data-step-target="eh-bus-fields"]')
    await tour.waitFor({ state: 'visible', timeout: 30_000 })
    check(await byId('eh-bus-fields').isVisible(), 'eh-bus-fields rendered (Bus card in Edit)')
    check(!(await byId('props-edit-bus').isVisible().catch(() => false)), 'Bus card is in Edit mode')
    check(!(await byId('eh-reference-design-panel').isVisible().catch(() => false)),
      'Results panel closed for the tour')
    await byId('guide-highlight').waitFor({ state: 'visible', timeout: 5_000 })
    ok('coach mark highlight drawn')
    check(!(await byId('guide-step-missing').isVisible().catch(() => false)),
      'no "not on screen" note')
    info(`tour title: ${(await byId('guide-step-title').innerText()).trim()}`)
    await shot(page, 'tagging-tour-bus-fields')
  } catch (e) {
    try { await shot(page, 'FAILURE') } catch { /* page gone */ }
    const logFile = path.join(args.out, 'FAILURE-console.log')
    fs.writeFileSync(logFile, consoleLines.join('\n'))
    info(`console log ${logFile}`)
    throw e
  } finally {
    await context.close()
  }
}

// ── main ────────────────────────────────────────────────────────────────────
let code = 0
let browser
try {
  console.log(`smoke-guided --phase ${args.phase} --template ${args.template}`)
  console.log(`out: ${args.out}   run dir: ${RUN}`)

  step('self-check: Playwright launches a headless browser')
  let chromium
  try {
    ({ chromium } = createRequire(import.meta.url)(PLAYWRIGHT))
    const b = await chromium.launch({ headless: true })
    const p = await b.newPage()
    await p.goto('about:blank')
    await b.close()
  } catch (e) {
    throw new ToolingError(`Playwright self-check failed: ${e.message}`)
  }
  ok(`playwright at ${PLAYWRIGHT}, browsers at ${process.env.PLAYWRIGHT_BROWSERS_PATH}`)

  step('start uvicorn (local mode, no API key, scratch dirs) and Vite')
  for (const port of [8000, 5173, STUB_PORT]) {
    if (!(await portFree(port))) throw new ToolingError(`port ${port} is already in use`)
  }
  const appData = path.join(RUN, 'appdata')
  const projects = path.join(RUN, 'projects')
  fs.mkdirSync(appData, { recursive: true })
  fs.mkdirSync(projects, { recursive: true })
  const env = { ...process.env,
    PYPSAGUI_LOCAL_MODE: '1', PYPSAGUI_APP_DATA_DIR: appData, PYPSAGUI_PROJECTS_ROOT: projects,
    PYTHONPATH: `${REPO}:${BACKEND}` }
  delete env.ANTHROPIC_API_KEY
  start('uvicorn', PYTHON, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'],
    { cwd: BACKEND, env })
  start('vite', path.join(FRONTEND, 'node_modules', '.bin', 'vite'),
    ['--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: FRONTEND, env: process.env })
  await waitFor(`${API}/api/health`, 'uvicorn', 180_000)
  await waitFor(`${WEB}/`, 'vite', 60_000, r => r.status < 500)
  ok('uvicorn :8000 and vite :5173 answering')

  browser = await chromium.launch({ headless: true })
  if (args.phase === 'P22.9') await phaseP229(browser)
  console.log(`\nPASS — ${shots.length} screenshots in ${args.out}`)
} catch (e) {
  code = e instanceof ToolingError ? 3 : 1
  console.error(`\n${code === 3 ? 'TOOLING FAILURE' : 'FAIL'} — ${e.message}`)
} finally {
  if (browser) await browser.close().catch(() => {})
  await stopAll()
  if (!args.keep) fs.rmSync(path.join(RUN, 'appdata'), { recursive: true, force: true })
  if (!args.keep) fs.rmSync(path.join(RUN, 'projects'), { recursive: true, force: true })
}
process.exit(code)
