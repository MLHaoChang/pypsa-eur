#!/usr/bin/env node
/**
 * Browser smoke for the guided-mode phases (spec
 * docs/superpowers/specs/2026-09-27-guided-mode.md §8.4; path per phase).
 *
 *   cd pypsa-gui/frontend
 *   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs \
 *     --phase P22.9|P23|P24-BE [--template eh_datacenter] [--out <dir>] [--keep]
 *
 * P24-BE re-runs the P22.9 (Expert) path unchanged and, around its study,
 * checks GET /api/results/eh_review with curl: 204 before, `running` while
 * the study runs, `ok` with `stale:false` after (plan P24-BE gate row 5).
 * The transcript goes to <out>/eh_review-curl.txt.
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
import { execFileSync, spawn } from 'node:child_process'
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
const PHASES = new Set(['P22.9', 'P23', 'P24-BE'])

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

// ── P24-BE: GET /api/results/eh_review with curl (plan gate row 5) ─────────
const reviewTranscript = []
function curlReview() {
  const url = `${API}/api/results/eh_review`
  const out = execFileSync('curl', ['-sS', '-w', '\n%{http_code}', url], { encoding: 'utf8' })
  const nl = out.lastIndexOf('\n')
  const status = Number(out.slice(nl + 1))
  const body = out.slice(0, nl)
  reviewTranscript.push(`$ curl -sS -w '\\n%{http_code}' ${url}`,
    body.length > 600 ? `${body.slice(0, 600)}… (${body.length} bytes)` : body, String(status), '')
  return { status, json: body ? JSON.parse(body) : null }
}
function saveReviewTranscript() {
  if (!reviewTranscript.length) return
  const f = path.join(args.out, 'eh_review-curl.txt')
  fs.writeFileSync(f, reviewTranscript.join('\n'))
  info(`eh_review transcript ${f}`)
}

// ── the stub model (every phase, spec §8.4 step 2) ─────────────────────────
async function activateStubProfile() {
  start('stub', PYTHON, [path.join(BACKEND, 'smoke', 'stub_openai_endpoint.py')], { cwd: BACKEND })
  await waitFor(`http://127.0.0.1:${STUB_PORT}/v1/models`, 'stub model', 30_000)
  await api('PUT', `/api/chat/settings/llm/profiles/${STUB_PROFILE}`, {
    label: 'Smoke stub', preset: 'custom', wire: 'openai',
    base_url: `http://127.0.0.1:${STUB_PORT}/v1`, model: 'stub-model',
    tools: true, vision: false, auth: 'none', fallback_model: null, max_output_tokens: null,
  })
  await api('POST', '/api/chat/settings/llm/active', { profile_id: STUB_PROFILE })
  const h = await api('GET', '/api/chat/health')
  check(h.chat_ready === true && h.active_profile?.id === STUB_PROFILE,
    `health: active_profile=${h.active_profile?.id} chat_ready=true`)
}

// Seeds localStorage once per context, before the app's first script runs
// (the store decides the Guided/Expert mode at import). Later navigations in
// the same context keep whatever the app itself wrote since.
async function seedStorageOnce(context, entries) {
  await context.addInitScript((kv) => {
    try {
      if (localStorage.getItem('smoke:seeded') === '1') return
      for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v)
      localStorage.setItem('smoke:seeded', '1')
    } catch { /* about:blank has no storage */ }
  }, entries)
}

// ── the P22.9 path (spec §2.11) ─────────────────────────────────────────────
async function phaseP229(browser, { reviewChecks = false } = {}) {
  const consoleLines = []
  const requests = []
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  // P22.9 is the Expert flow. Since P23 a fresh profile is a first-time user
  // and starts in Guided (spec §3.2), where the sidebar's Results entry this
  // path clicks is hidden — so this context is a user who chose Expert.
  await seedStorageOnce(context, {
    'network-diagram:ui-mode': 'expert',
    'network-diagram:ui-mode-explicit': '1',
  })
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
    await activateStubProfile()
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
    check((await byId('ui-mode-expert').getAttribute('aria-pressed')) === 'true',
      'Expert mode (explicit choice) survives creating the template project')
    check(!(await byId('new-project-wizard').isVisible().catch(() => false)), 'wizard closed')
    check(!(await page.getByRole('button', { name: /^Resume/ }).isVisible().catch(() => false)),
      'no "Resume" step on the way in')
    await shot(page, 'workbench-opened')
    check(!requests.some(u => u.includes('/api/projects/unclaimed')),
      'local mode never requested /api/projects/unclaimed (bug 7)')

    const before = await snapshotTables()
    info(`live tables: ${TABLES.map(t => `${t}=${before[t].length}`).join(' ')}`)

    if (reviewChecks) {
      step('P24-BE: GET /api/results/eh_review before any study → 204')
      const r0 = curlReview()
      check(r0.status === 204 && r0.json === null, `eh_review ${r0.status} with an empty body`)
    }

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
    if (reviewChecks) {
      step('P24-BE: GET /api/results/eh_review during the study → 200 running')
      const rec = await api('GET', '/api/results/eh_study')
      info(`eh_study status at this moment: ${rec?.status}`)
      const r1 = curlReview()
      check(r1.status === 200 && r1.json?.status === 'running',
        `eh_review ${r1.status} status=${r1.json?.status}`)
      check(typeof r1.json?.message === 'string' && r1.json.message.length > 0,
        `running message: "${r1.json?.message}"`)
    }

    step('study finishes → finished cue → report in view')
    const cue = byId('eh-study-finished-cue')
    await cue.waitFor({ state: 'visible', timeout: 10 * 60_000 })
    const study = await api('GET', '/api/results/eh_study')
    check(study?.status === 'done', `study status done (${study?.archetype})`)
    check((await cue.textContent()).trim() === 'Study finished — view report', 'cue text')
    if (reviewChecks) {
      step('P24-BE: GET /api/results/eh_review after the study → 200 ok, stale:false')
      const r2 = curlReview()
      check(r2.status === 200 && r2.json?.status === 'ok', `eh_review ${r2.status} status=${r2.json?.status}`)
      check(r2.json?.stale === false, `stale=${r2.json?.stale} (source "${r2.json?.source}")`)
      check(Array.isArray(r2.json?.findings) && r2.json?.summary
        && r2.json.summary.archetype === study?.archetype,
        `summary.archetype=${r2.json?.summary?.archetype}, ${r2.json?.findings?.length} findings, ` +
        `verdict=${r2.json?.summary?.verdict}`)
    }
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
    // The API says done; wait until the tab's own 2 s poll has caught up
    // (button back to "Run B/C sweep" and class-B rows in the table) before
    // the screenshot — `fmea-table` alone already existed before the sweep.
    await page.waitForFunction(() => {
      const btn = document.querySelector('[data-testid="fmea-sweep"]')
      const classB = [...document.querySelectorAll('[data-testid="fmea-table"] tbody tr')]
        .some(tr => tr.querySelectorAll('td')[1]?.textContent?.trim() === 'B')
      return btn && !btn.disabled && btn.textContent.includes('Run B/C sweep') && classB
    }, null, { timeout: 30_000 })
    ok('FMEA tab shows the finished sweep (button re-enabled, class-B rows)')
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
    saveReviewTranscript()
    await context.close()
  }
}

// ── the P23 path (spec §3, plan P23 gate item 5) ───────────────────────────
const MODE_KEY = 'network-diagram:ui-mode'
const EXPLICIT_KEY = 'network-diagram:ui-mode-explicit'
// PROJECT rows Guided hides (spec §3.5); Save / Recent / Projects home stay.
const GUIDED_HIDDEN_PROJECT_ROWS = ['Project info', 'Snapshots', 'Scenarios', 'Duplicate project',
  'Export bundle', 'Workspace panel']

async function phaseP23(browser) {
  const consoleLines = []
  const opened = []
  async function freshPage(seed) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    opened.push(context)
    if (seed) await seedStorageOnce(context, seed)
    const page = await context.newPage()
    page.on('console', m => consoleLines.push(`[${m.type()}] ${m.text()}`))
    page.on('pageerror', e => consoleLines.push(`[pageerror] ${e.message}`))
    return page
  }
  let page = null
  const byId = id => page.locator(`[data-testid="${id}"]`)
  const stored = () => page.evaluate(([m, e]) => ({
    mode: localStorage.getItem(m), explicit: localStorage.getItem(e),
  }), [MODE_KEY, EXPLICIT_KEY])
  const pressed = async m => (await byId(`ui-mode-${m}`).getAttribute('aria-pressed')) === 'true'
  const sidebar = () => page.locator('aside').first()
  const resultsTabIds = () => page.evaluate(() =>
    [...document.querySelectorAll('[data-testid^="results-tab-"]')]
      .map(el => el.getAttribute('data-testid').slice('results-tab-'.length)))

  // A template creates a project under the template's fixed name, and a
  // second creation of the same template on one backend is a 409 — so each
  // creation in this path uses its own template.
  async function fromTemplate(templateId) {
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: /From template/ }).first().click()
    await byId('new-project-wizard').waitFor({ state: 'visible', timeout: 15_000 })
    await page.getByRole('button', { name: new RegExp(TEMPLATE_NAMES[templateId]) }).click()
    await page.waitForURL(/\/app\?project=/, { timeout: 60_000 })
    const name = new URL(page.url()).searchParams.get('project') ?? TEMPLATE_NAMES[templateId]
    await byId('ui-mode-switch').waitFor({ state: 'visible', timeout: 30_000 })
    return name
  }
  async function blank(name) {
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: 'New project', exact: true }).first().click()
    await byId('new-project-wizard').waitFor({ state: 'visible', timeout: 15_000 })
    const input = page.getByPlaceholder('my_project')
    await input.fill(name)
    await input.press('Enter')
    await page.waitForURL(/\/app\?project=/, { timeout: 60_000 })
    const got = new URL(page.url()).searchParams.get('project') ?? name
    await byId('ui-mode-switch').waitFor({ state: 'visible', timeout: 30_000 })
    return got
  }
  async function palette(title) {
    await page.keyboard.press('Control+k')
    await page.getByPlaceholder(/Type a command/i).fill(title)
    await page.getByText(title, { exact: true }).first().click()
  }
  async function assertGuidedChrome() {
    check(await pressed('guided') && !(await pressed('expert')), 'ui-mode-guided is pressed')
    check(await byId('sidebar-assistant').isVisible(), 'sidebar: Assistant shown')
    check(await byId('sidebar-hub-design').isVisible(), 'sidebar: Hub design shown')
    check(await byId('sidebar-section-project').isVisible(), 'sidebar: PROJECT shown')
    for (const id of ['sidebar-section-data', 'sidebar-section-simulation', 'sidebar-mode-switcher']) {
      check((await byId(id).count()) === 0, `sidebar: ${id} not in the DOM`)
    }
    for (const label of GUIDED_HIDDEN_PROJECT_ROWS) {
      check((await sidebar().getByRole('button', { name: label, exact: true }).count()) === 0,
        `sidebar: "${label}" not in the DOM`)
    }
    for (const label of ['Save', 'Projects home']) {
      check(await sidebar().getByRole('button', { name: label, exact: true }).first().isVisible(),
        `sidebar: "${label}" shown`)
    }
    check((await sidebar().getByRole('button', { name: 'Solve Queue' }).count()) === 0,
      'sidebar: Solve Queue row not in the DOM')
  }
  async function assertExpertChrome() {
    check(await pressed('expert') && !(await pressed('guided')), 'ui-mode-expert is pressed')
    for (const id of ['sidebar-section-project', 'sidebar-section-data', 'sidebar-section-simulation',
      'sidebar-mode-switcher']) {
      check(await byId(id).isVisible(), `sidebar: ${id} visible`)
    }
    check((await byId('sidebar-hub-design').count()) === 0, 'sidebar: no Hub design row')
    for (const label of [...GUIDED_HIDDEN_PROJECT_ROWS, 'Solve Queue']) {
      check(await sidebar().getByRole('button', { name: label }).first().isVisible(),
        `sidebar: "${label}" shown`)
    }
  }

  try {
    step('stub model profile (every phase)')
    await activateStubProfile()

    // ── A. first-time user ───────────────────────────────────────────────
    step('fresh profile (first-time user) → Guided, persisted implicitly')
    page = await freshPage()
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: 'New project', exact: true }).first().waitFor({ timeout: 30_000 })
    const s0 = await stored()
    check(s0.mode === 'guided' && s0.explicit === null,
      `first load stored ui-mode=${s0.mode}, explicit=${s0.explicit}`)
    await shot(page, 'p23-first-time-projects')

    step(`first-time user creates ${args.template} → Guided workbench, hubDesign open`)
    const others = Object.keys(TEMPLATE_NAMES).filter(t => t !== args.template)
    const tpl1 = await fromTemplate(args.template)
    info(`project ${tpl1}`)
    await byId('hub-design-panel').waitFor({ state: 'visible', timeout: 30_000 })
    ok('hub-design-panel auto-opened')
    await assertGuidedChrome()
    await shot(page, 'p23-guided-template-hubdesign')

    step('Guided Results shows exactly adequacy + FMEA (via the palette)')
    await palette('Open results panel')
    await byId('results-tab-adequacy').waitFor({ state: 'visible', timeout: 30_000 })
    const guidedTabs = await resultsTabIds()
    check(JSON.stringify(guidedTabs) === JSON.stringify(['adequacy', 'fmea']),
      `results tabs: ${guidedTabs.join(', ')}`)
    await shot(page, 'p23-guided-results-two-tabs')

    step('a hidden panel stays reachable through the palette')
    await palette('Open solver settings')
    await page.getByText('Solver settings', { exact: true }).first().waitFor({ state: 'visible', timeout: 15_000 })
    ok('Solver settings panel opened in Guided')
    await shot(page, 'p23-guided-palette-solver')

    step('switch to Expert → everything back')
    await palette('Open results panel')
    await byId('results-tab-adequacy').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('ui-mode-expert').click()
    await byId('sidebar-section-data').waitFor({ state: 'visible', timeout: 10_000 })
    await assertExpertChrome()
    const expertTabs = await resultsTabIds()
    check(expertTabs.length >= 12 && ['dispatch', 'prices', 'adequacy', 'fmea', 'asset'].every(t => expertTabs.includes(t)),
      `results tabs back (${expertTabs.length}): ${expertTabs.join(', ')}`)
    const s1 = await stored()
    check(s1.mode === 'expert' && s1.explicit === '1', `stored ui-mode=${s1.mode}, explicit=${s1.explicit}`)
    await shot(page, 'p23-expert-everything')

    step('reload keeps the explicit Expert choice')
    await page.reload({ waitUntil: 'domcontentloaded' })
    await byId('ui-mode-switch').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('sidebar-section-data').waitFor({ state: 'visible', timeout: 30_000 })
    await assertExpertChrome()
    await sleep(1000)
    check((await byId('hub-design-panel').count()) === 0, 'Expert never auto-opens hubDesign')
    await shot(page, 'p23-expert-after-reload')

    step('explicit Expert creates a blank and a template project → stays Expert')
    const blankX = await blank('smoke_p23_explicit_blank')
    info(`project ${blankX}`)
    await byId('sidebar-section-data').waitFor({ state: 'visible', timeout: 30_000 })
    check(await pressed('expert'), 'blank project: still Expert')
    const tplX = await fromTemplate(others[0])
    info(`project ${tplX}`)
    await byId('sidebar-section-data').waitFor({ state: 'visible', timeout: 30_000 })
    check(await pressed('expert'), 'template project: still Expert')
    await sleep(1000)
    check((await byId('hub-design-panel').count()) === 0, 'no hubDesign auto-open in Expert')
    const s2 = await stored()
    check(s2.mode === 'expert' && s2.explicit === '1', `stored ui-mode=${s2.mode}, explicit=${s2.explicit}`)
    await shot(page, 'p23-explicit-expert-new-project')

    // ── B. existing user (implicit Expert) → blank project ────────────────
    step('existing user (seeded current-project) → Expert, nothing written')
    page = await freshPage({ 'network-diagram:current-project': blankX })
    await page.goto(`${WEB}/app`, { waitUntil: 'domcontentloaded' })
    await byId('ui-mode-switch').waitFor({ state: 'visible', timeout: 30_000 })
    check(await pressed('expert'), 'existing user starts in Expert')
    const s3 = await stored()
    check(s3.mode === null && s3.explicit === null, `nothing stored (ui-mode=${s3.mode})`)
    await shot(page, 'p23-existing-user-expert')

    step('implicit Expert creates a blank project → Guided')
    const blankI = await blank('smoke_p23_implicit_blank')
    info(`project ${blankI}`)
    await byId('hub-design-panel').waitFor({ state: 'visible', timeout: 30_000 })
    await assertGuidedChrome()
    const s4 = await stored()
    check(s4.mode === 'guided' && s4.explicit === null, `stored ui-mode=${s4.mode}, explicit=${s4.explicit}`)
    await shot(page, 'p23-implicit-blank-guided')

    // ── C. existing user (implicit Expert) → template project ─────────────
    step(`implicit Expert creates ${others[1]} → Guided, hubDesign open`)
    page = await freshPage({ 'network-diagram:current-project': blankX })
    await page.goto(`${WEB}/app`, { waitUntil: 'domcontentloaded' })
    await byId('ui-mode-switch').waitFor({ state: 'visible', timeout: 30_000 })
    check(await pressed('expert'), 'existing user starts in Expert')
    const tplI = await fromTemplate(others[1])
    info(`project ${tplI}`)
    await byId('hub-design-panel').waitFor({ state: 'visible', timeout: 30_000 })
    await assertGuidedChrome()
    await shot(page, 'p23-implicit-template-guided')

    step('Guided hidden panel is pruned on switching; closing hubDesign is respected')
    await byId('ui-mode-expert').click()
    await byId('sidebar-section-simulation').waitFor({ state: 'visible', timeout: 10_000 })
    await sidebar().getByRole('button', { name: 'Solver Settings' }).first().click()
    await page.getByText('Solver settings', { exact: true }).first().waitFor({ state: 'visible', timeout: 15_000 })
    await byId('ui-mode-guided').click()
    await byId('hub-design-panel').waitFor({ state: 'visible', timeout: 10_000 })
    ok('solver settings → hubDesign on switching to Guided')
    await page.getByTitle('Close (Esc)').first().click()
    await sleep(1500)
    check((await byId('hub-design-panel').count()) === 0, 'closed hubDesign stays closed')
    await shot(page, 'p23-guided-closed-hubdesign')

    // ── D. Guided entry points (gate P23 B2/B3, spec §10 addendum) ─────────
    step('Expert on Results → switch to Guided → Results stays; closing it opens no hubDesign')
    await byId('ui-mode-expert').click()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await byId('sidebar-section-simulation').waitFor({ state: 'visible', timeout: 30_000 })
    await sidebar().getByRole('button', { name: 'Results', exact: true }).first().click()
    await byId('results-tab-adequacy').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('ui-mode-guided').click()
    await byId('sidebar-hub-design').waitFor({ state: 'visible', timeout: 10_000 })
    check(await byId('results-tab-adequacy').isVisible(), 'Results stays open on switching to Guided')
    await page.getByTitle('Close (Esc)').first().click()
    await sleep(1500)
    check((await byId('hub-design-panel').count()) === 0,
      'no hubDesign after closing a panel that was open in Guided (the project counts as auto-opened)')

    step('Guided: Properties "View results" → Asset Detail as a temporary advanced tab')
    const gens = await api('GET', '/api/network/generators')
    check(gens.length > 0, `network has generators (${gens.length})`)
    const gen = gens[0].name
    await page.keyboard.press('Control+k')
    await page.getByPlaceholder(/Type a command/i).fill(gen)
    await page.getByText(gen, { exact: true }).first().click()
    await page.getByRole('button', { name: 'View results' }).first().click()
    const assetTab = byId('results-tab-asset')
    await assetTab.waitFor({ state: 'visible', timeout: 30_000 })
    check((await assetTab.getAttribute('data-advanced')) === 'true', 'Asset Detail tab is marked advanced')
    check((await assetTab.getAttribute('class')).includes('border-accent'), 'Asset Detail is the active tab')
    check(/advanced/i.test(await assetTab.innerText()), 'the chip reads "Advanced"')
    const withChip = await resultsTabIds()
    check(JSON.stringify(withChip) === JSON.stringify(['adequacy', 'fmea', 'asset']),
      `strip: ${withChip.join(', ')}`)
    await shot(page, 'p23-guided-asset-detail-advanced')
    await byId('results-tab-adequacy').click()
    await sleep(300)
    const afterPick = await resultsTabIds()
    check(JSON.stringify(afterPick) === JSON.stringify(['adequacy', 'fmea']),
      `picking Adequacy removes the advanced tab (${afterPick.join(', ')})`)

    step('Guided: tagging tour from Results → Properties bus card, not hubDesign')
    const toggle = byId('eh-reference-design-toggle')
    await toggle.waitFor({ timeout: 30_000 })
    if (!(await byId('eh-tagging-guide-button').isVisible().catch(() => false))) await toggle.click()
    await byId('eh-tagging-guide-button').click()
    const tour = page.locator('[data-testid="guide-tour"][data-step-target="eh-bus-fields"]')
    await tour.waitFor({ state: 'visible', timeout: 30_000 })
    await sleep(800)
    check((await byId('hub-design-panel').count()) === 0, 'hubDesign did not open over the tour')
    check(await byId('eh-bus-fields').isVisible(), 'eh-bus-fields rendered (Bus card in Edit)')
    await byId('guide-highlight').waitFor({ state: 'visible', timeout: 5_000 })
    ok('coach mark highlight drawn')
    check(!(await byId('guide-step-missing').isVisible().catch(() => false)), 'no "not on screen" note')
    check(await pressed('guided'), 'still Guided')
    await shot(page, 'p23-guided-tagging-tour')
  } catch (e) {
    try { if (page) await shot(page, 'FAILURE') } catch { /* page gone */ }
    const logFile = path.join(args.out, 'FAILURE-console.log')
    fs.writeFileSync(logFile, consoleLines.join('\n'))
    info(`console log ${logFile}`)
    throw e
  } finally {
    for (const c of opened) await c.close().catch(() => {})
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
  else if (args.phase === 'P24-BE') await phaseP229(browser, { reviewChecks: true })
  else if (args.phase === 'P23') await phaseP23(browser)
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
