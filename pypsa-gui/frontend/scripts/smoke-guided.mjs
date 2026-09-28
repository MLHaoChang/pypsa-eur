#!/usr/bin/env node
/**
 * Browser smoke for the guided-mode phases (spec
 * docs/superpowers/specs/2026-09-27-guided-mode.md §8.4; path per phase).
 *
 *   cd pypsa-gui/frontend
 *   PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs \
 *     --phase P22.9|P23|P24-BE|P24|P25 [--template eh_datacenter] [--out <dir>] [--keep]
 *
 * P24 walks the Guided hub design (spec §5) as a first-time user: the
 * template from /projects opens hubDesign at Site; Site rows match the
 * readiness body; Goal shows the pack default; Run → running → done → the
 * rail moves to Results with the §5.5 headline; Open full report; Improve
 * lists findings; "Let the assistant do this" sends the request (P25: a user
 * message, then the stub's confirmation card, declined); Check risks (FMEA) runs the sweep and opens the FMEA tab; the
 * hub_design tour walks its steps. Then the other two templates are created
 * from the Start card: the H2 hub (no goal → "No reliability goal…") and the
 * island microgrid (off-grid wording on Site, its own verdict). Live tables
 * are compared around every study and the sweep.
 *
 * P24-BE re-runs the P22.9 (Expert) path unchanged and, around its study,
 * checks GET /api/results/eh_review with curl: 204 before, `running` while
 * the study runs, `ok` with `stale:false` after (plan P24-BE gate row 5).
 * The transcript goes to <out>/eh_review-curl.txt.
 *
 * P25 (spec §6, §8.4; plan P25 row 5) — the assistant does the steps. The
 * stub model replies after a delay so a turn is visibly streaming. Data
 * center, Guided: Improve → "Let the assistant do this" → the request is a
 * user message sent once (a double click is deduped), the stream body carries
 * ui_context.ui_mode = guided and guided_step = improve and no attachments,
 * and the stub's recorded last user text contains "Guided mode is on"; a
 * second card click while that turn streams is queued, the stub's scripted
 * tool call (§6.6) renders a confirmation card, the user declines, and only
 * then is the queued request sent. The bubble shows a plain label with the
 * sent text in a collapsed Details, and the transcript never scrolls sideways
 * (gate B2). Expert: a typed message's body has no ui_mode and the recorded
 * text no addendum. The template's tags are stripped and suggest_eh_setup
 * (run through the stub) recovers them without writing anything. Gate B1: the
 * Site card's grid fix in Guided stops at an update_component card (write
 * tier) and a denial changes nothing; the same request in Expert applies
 * directly.
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
const PHASES = new Set(['P22.9', 'P23', 'P24-BE', 'P24', 'P25'])

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
async function activateStubProfile({ replyDelayMs = 0 } = {}) {
  // SMOKE_STUB: a stub script to use instead of the committed one (a
  // reviewer's scratch probe, or a branch not landed yet).
  start('stub', PYTHON, [process.env.SMOKE_STUB ?? path.join(BACKEND, 'smoke', 'stub_openai_endpoint.py')],
    { cwd: BACKEND, env: { ...process.env, STUB_REPLY_DELAY_MS: String(replyDelayMs) } })
  await waitFor(`http://127.0.0.1:${STUB_PORT}/v1/models`, 'stub model', 30_000)
  await api('PUT', `/api/chat/settings/llm/profiles/${STUB_PROFILE}`, {
    label: 'Smoke stub', preset: 'custom', wire: 'openai',
    base_url: `http://127.0.0.1:${STUB_PORT}/v1`, model: 'stub-model',
    tools: true, vision: false, auth: 'none', fallback_model: null, max_output_tokens: null,
  })
  await api('POST', '/api/chat/settings/llm/active', { profile_id: STUB_PROFILE })
  stubSeenBase = (await stubRequests()).length
  const h = await api('GET', '/api/chat/health')
  check(h.chat_ready === true && h.active_profile?.id === STUB_PROFILE,
    `health: active_profile=${h.active_profile?.id} chat_ready=true`)
}

// What the stub model received (test-only route, §6.6): per request the last
// user text and the raw payload.
let stubSeenBase = 0
async function stubRequests() {
  const r = await fetch(`http://127.0.0.1:${STUB_PORT}/_stub/requests`)
  if (!r.ok) throw new Error(`stub /_stub/requests → ${r.status}`)
  return r.json()
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

// ── the P24 path (spec §5, plan P24-FE gate item 5) ────────────────────────
// The §5.5 headline, recomputed from the review / report bodies the app
// read, so the smoke checks the rendered sentence character for character.
function expectedHeadline(review, report) {
  const num = v => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const g = v => String(Number(v.toPrecision(6)))
  const s = review.summary ?? {}
  const verdict = typeof s.verdict === 'string' ? s.verdict : null
  const lole = num(s.mc_lole_h_per_year) ?? num(report?.mc_lole_h)
  const target = num(s.target_lole_h)
  const rows = report?.sections?.fmea_top?.payload?.rows ?? []
  const cert = report?.sections?.certification?.payload ?? {}
  if (verdict === 'fail' && lole != null && target != null) {
    const top = rows[0] ? String(rows[0].name || rows[0].mode_id) : "the plan's energy limit"
    return `Not certified: about ${lole.toFixed(0)} h/yr of shortfall vs a ${g(target)} h/yr goal — driven by ${top}`
  }
  if (verdict === 'inconclusive' && target != null) {
    let ci = null
    const yrs = num(cert.horizon_years)
    if (Array.isArray(cert.lole_ci) && yrs && yrs > 0) ci = [cert.lole_ci[0] / yrs, cert.lole_ci[1] / yrs]
    if (!ci) {
      for (const f of review.findings ?? []) {
        const raw = f.evidence?.lole_ci_per_horizon
        if (Array.isArray(raw)) { ci = raw; break }
      }
    }
    if (ci) return `Not decided: the shortfall estimate (${ci[0].toFixed(0)}–${ci[1].toFixed(0)} h/yr) straddles the ${g(target)} h/yr goal — more simulation runs would settle it.`
  }
  if (verdict === 'pass' && lole != null && target != null) {
    return `Certified: about ${lole.toFixed(1)} h/yr of shortfall, under the ${g(target)} h/yr goal.`
  }
  if (lole != null && target == null) {
    return `No reliability goal was set — the study reports ${lole.toFixed(1)} h/yr of shortfall. Set a goal to certify.`
  }
  if (lole == null && target == null) {
    return 'No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict.'
  }
  const note = report?.sections?.certification?.note
  return `The study could not certify reliability: ${note ? String(note) : 'the reliability check was not part of this run.'}`
}

async function phaseP24(browser) {
  const consoleLines = []
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.on('console', m => consoleLines.push(`[${m.type()}] ${m.text()}`))
  page.on('pageerror', e => consoleLines.push(`[pageerror] ${e.message}`))
  const byId = id => page.locator(`[data-testid="${id}"]`)
  const railState = async s => byId(`hub-rail-step-${s}`).getAttribute('data-state')
  const waitRail = (s, state, timeout = 30_000) => page.waitForFunction(([id, st]) =>
    document.querySelector(`[data-testid="${id}"]`)?.getAttribute('data-state') === st,
  [`hub-rail-step-${s}`, state], { timeout })
  const textOf = async id => ((await byId(id).textContent()) ?? '').trim()
  const verdicts = {}

  async function readinessFor(templateId, archetype) {
    const meta = await api('GET', `/api/projects/${encodeURIComponent(TEMPLATE_NAMES[templateId])}/eh_template`)
    const po = { ...(meta?.pack_overrides ?? {}) }
    if (archetype !== 'weak_flexible') { delete po.import_p_nom_mw; delete po.import_energy_mwh_per_year }
    const q = new URLSearchParams({ archetype })
    if (Object.keys(po).length) q.set('pack_overrides', JSON.stringify(po))
    return api('GET', `/api/results/eh_readiness?${q}`)
  }

  async function checkSite(templateId, archetype) {
    await byId('hub-site-grid').waitFor({ state: 'visible', timeout: 60_000 })
    check((await byId('hub-site-type').inputValue()) === archetype, `site type = ${archetype}`)
    const r = await readinessFor(templateId, archetype)
    const grid = await textOf('hub-site-grid')
    const critical = await textOf('hub-site-critical')
    const strength = await textOf('hub-site-strength')
    const outage = await textOf('hub-site-outage')
    info(`Site: ${grid} | ${critical} | ${strength} | ${outage}`)
    for (const l of r.import.links) check(grid.includes(l), `grid row names ${l}`)
    if (archetype === 'off_grid') {
      check(/island/.test(grid) && !/\d MW/.test(grid), 'off-grid: the tie is "normally open", no MW rating shown (N4)')
      check(!/without/.test(outage), 'off-grid: the open tie is not reported as missing outage data (N4)')
      check((await byId('hub-site-fix-grid').count()) === 0 && (await byId('hub-site-fix-outage').count()) === 0,
        'off-grid: no grid / outage fix buttons')
    } else if (r.import_p_nom_mw != null) {
      check(grid.includes(`(${Number(r.import_p_nom_mw.toPrecision(6))} MW)`), `grid row shows ${r.import_p_nom_mw} MW`)
    }
    for (const b of r.critical_buses) check(critical.includes(b), `critical row names ${b}`)
    const wantStrength = r.scr.status === 'ok' ? 'data present'
      : r.scr.status === 'not_required' ? 'not needed' : 'data missing'
    check(strength.includes(wantStrength), `strength row: scr ${r.scr.status} → "${wantStrength}"`)
    check(outage.includes(`${r.outage_units.count} units`), `outage row: ${r.outage_units.count} units`)
    return r
  }

  async function runStudy(label, { expectLole, tourWhileRunning = false }) {
    await byId('hub-rail-step-goal').click()
    await byId('hub-goal-lole').waitFor({ state: 'visible', timeout: 15_000 })
    await page.waitForFunction(v =>
      document.querySelector('[data-testid="hub-goal-lole"]')?.value === v, expectLole, { timeout: 30_000 })
    ok(`Goal shows the pack default "${expectLole}"`)
    await page.waitForFunction(() => /€[\d,]+ per MWh/.test(
      document.querySelector('[data-testid="hub-goal-voll"]')?.textContent ?? ''), null, { timeout: 15_000 })
    info(`VOLL line: ${await textOf('hub-goal-voll')}`)
    await shot(page, `p24-${label}-goal`)
    const before = await snapshotTables()
    await byId('hub-goal-run').click()
    await byId('hub-goal-running').waitFor({ state: 'visible', timeout: 30_000 })
    check(await byId('hub-rail-spinner').isVisible(), 'rail spinner on Goal while running')
    check(await railState('results') === 'blocked' && await railState('improve') === 'blocked',
      'Results / Improve blocked while running')
    const runText = await textOf('hub-goal-running')
    check(runText.startsWith('Studying…') && !/poll|get_adequacy_results/.test(runText),
      `running text is the card's own: "${runText}" (N3)`)
    await shot(page, `p24-${label}-running`)
    if (tourWhileRunning) {
      // Gate B2: the tour's rail clicks are not manual moves, so the jump to
      // Results still happens when the study finishes after the tour.
      info('walking the tour while the study runs')
      await walkTour(PRE_STUDY_TOUR)
    }
    await byId('hub-card-results').waitFor({ state: 'visible', timeout: 10 * 60_000 })
    check(await railState('results') === 'current', 'done → the rail moved to Results (auto-advance)')
    const study = await api('GET', '/api/results/eh_study')
    check(study?.status === 'done', `study done (${study?.archetype})`)
    const review = await api('GET', '/api/results/eh_review')
    const report = study.report ?? await api('GET', '/api/results/eh_reference_design')
    await byId('hub-results-verdict').waitFor({ state: 'visible', timeout: 30_000 })
    const got = await textOf('hub-results-verdict')
    const want = expectedHeadline(review, report)
    verdicts[label] = { verdict: review.summary?.verdict ?? null, headline: got }
    info(`verdict=${review.summary?.verdict} stale=${review.stale}`)
    check(got === want, `headline (§5.5): "${got}"`)
    check(review.stale === false && (await byId('hub-results-stale').count()) === 0, 'no stale banner')
    const diffs = tableDiffs(before, await snapshotTables())
    check(diffs.diffs.length === 0, `buses/links/generators equal after the study (*_nom_opt changes: ${diffs.nomOpt})`)
    await shot(page, `p24-${label}-results`)
    return { review, report }
  }

  async function walkTour(expectTargets) {
    await byId('hub-guide-button').click()
    const seen = []
    for (let i = 0; i < 20; i++) {
      const tour = byId('guide-tour')
      await tour.waitFor({ state: 'visible', timeout: 15_000 })
      const target = await tour.getAttribute('data-step-target')
      await sleep(250)   // a reveal renders on the next commit (GuidedTour retries after 60 ms)
      const missing = await byId('guide-step-missing').isVisible().catch(() => false)
      check(!missing, `tour step ${seen.length + 1}: ${target} on screen`)
      seen.push(target)
      const next = byId('guide-next')
      const last = ((await next.textContent()) ?? '').trim() === 'Done'
      await next.click()
      if (last) break
      await page.waitForFunction(t =>
        document.querySelector('[data-testid="guide-tour"]')?.getAttribute('data-step-target') !== t,
      target, { timeout: 10_000 })
    }
    check(JSON.stringify(seen) === JSON.stringify(expectTargets), `tour walked ${seen.length} steps: ${seen.join(', ')}`)
    return seen
  }

  const PRE_STUDY_TOUR = ['hub-rail', 'hub-start-templates', 'hub-site-readiness', 'hub-site-type',
    'hub-goal-lole', 'hub-goal-run']

  try {
    step('stub model profile (every phase)')
    await activateStubProfile()

    // ── 1. Start: the data center from /projects, first-time user ────────
    step('fresh profile → Guided; data center template → workbench with hub-design-panel at Site')
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: /From template/ }).first().waitFor({ timeout: 30_000 })
    check(await page.evaluate(k => localStorage.getItem(k), MODE_KEY) === 'guided', 'first-time user is Guided')
    await page.getByRole('button', { name: /From template/ }).first().click()
    await byId('new-project-wizard').waitFor({ state: 'visible', timeout: 15_000 })
    await page.getByRole('button', { name: new RegExp(TEMPLATE_NAMES.eh_datacenter) }).click()
    await page.waitForURL(/\/app\?project=/, { timeout: 60_000 })
    await byId('hub-design-panel').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('hub-card-site').waitFor({ state: 'visible', timeout: 30_000 })
    check(await railState('site') === 'current' && await railState('start') === 'done',
      'rail: Start ✓, Site current')
    check(await railState('results') === 'blocked', 'Results blocked before a study')
    check((await page.getByTitle(/queues the solve/).count()) === 0, 'Guided: no idle "Run LOPF" in the header')
    await shot(page, 'p24-dc-opened-at-site')

    step('hub_design tour before any study (gate B2): every shown step on screen, no dead end')
    await walkTour(PRE_STUDY_TOUR)
    await byId('hub-rail-step-site').click()

    step('Start card: three templates, the project\'s provenance')
    await byId('hub-rail-step-start').click()
    await byId('hub-start-templates').waitFor({ state: 'visible', timeout: 15_000 })
    const tplCount = await page.locator('[data-testid^="hub-start-template-"]').count()
    check(tplCount === 3, `${tplCount} energy-hub templates offered`)
    const prov = await textOf('hub-start-provenance')
    check(prov.includes(TEMPLATE_NAMES.eh_datacenter) && /synthetic|example/i.test(prov),
      `provenance: "${prov.slice(0, 120)}…"`)
    await shot(page, 'p24-dc-start-card')

    // ── 2. Site ───────────────────────────────────────────────────────────
    step('Site: grid link, critical bus, strength, outage count from readiness')
    await byId('hub-rail-step-site').click()
    await checkSite('eh_datacenter', 'weak_flexible')
    await shot(page, 'p24-dc-site')

    // ── 3–5. Goal → running → Results ─────────────────────────────────────
    step('Goal (pack default) → Run → running → done → Results headline')
    const dc = await runStudy('dc', { expectLole: '3' })
    const cost = await textOf('hub-results-cost').catch(() => '')
    check(cost.startsWith('Yearly cost of this design (before any shortfall costs)') && !/goal/i.test(cost),
      `cost line (gate B1): "${cost}"`)
    const risks = await page.locator('[data-testid^="hub-results-risks-"]').count()
    info(`risks shown: ${risks}; gaps: ${await byId('hub-results-gaps').count() ? await textOf('hub-results-gaps') : '(none)'}`)

    step('Open full report → Results → Adequacy, eh-report scrolled into view')
    await byId('hub-results-open-report').click()
    await byId('eh-report').waitFor({ state: 'visible', timeout: 30_000 })
    await sleep(1200)
    const inView = await page.evaluate(() => {
      const r = document.querySelector('[data-testid="eh-report"]')?.getBoundingClientRect()
      return !!r && r.top >= -2 && r.top < window.innerHeight
    })
    check(inView, 'eh-report in view')
    check((await byId('results-tab-adequacy').getAttribute('class')).includes('border-accent'), 'Adequacy tab active')
    await shot(page, 'p24-dc-full-report')
    await byId('sidebar-hub-design').click()
    await byId('hub-card-results').waitFor({ state: 'visible', timeout: 15_000 })

    // ── 6. Improve ────────────────────────────────────────────────────────
    step('Improve lists the high / medium findings; Why; Let the assistant do this sends (P25) → card → decline')
    await byId('hub-rail-step-improve').click()
    await byId('hub-improve-list').waitFor({ state: 'visible', timeout: 15_000 })
    const items = await byId('hub-improve-list').locator(':scope > li').count()
    const hm = dc.review.findings.filter(f => f.severity === 'high' || f.severity === 'medium')
    check(items >= 1 && items === hm.length, `${items} findings listed (${hm.map(f => f.id).join(', ')})`)
    const withAction = hm.find(f => f.actions?.length)
    const first = withAction ?? hm[0]
    await byId(`hub-improve-why-${first.id}`).click()
    check(/technical evidence/i.test(await textOf(`hub-improve-evidence-${first.id}`)), 'Why shows the technical evidence')
    if (withAction) {
      // Since P25 the button SENDS (§5.7): the §5.7 text becomes a user
      // message, the stub's scripted call a confirmation card; decline it.
      await byId(`hub-improve-do-${withAction.id}`).click()
      const want = `Run the tool ${withAction.actions[0].tool} with exactly these arguments: ${JSON.stringify(withAction.actions[0].args)}`
      await page.waitForFunction(w => [...document.querySelectorAll('[data-testid="chat-message"][data-role="user"]')]
        .some(m => (m.textContent ?? '').includes(w)), want, { timeout: 15_000 })
      ok('the §5.7 action text was sent as a user message')
      await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 30_000 })
      ok('the stub\'s scripted call rendered a confirmation card')
      await shot(page, 'p24-dc-improve-delegate-card')
      await byId('chat-confirm-deny').click()
      await byId('chat-confirmation-card').waitFor({ state: 'detached', timeout: 30_000 })
      ok('declined')
    } else {
      info('no high / medium finding carries an action on this run')
    }
    await shot(page, 'p24-dc-improve')

    // ── 7. Check risks (FMEA) ─────────────────────────────────────────────
    step('Check risks (FMEA) runs the sweep → FMEA tab with rows; live tables equal')
    const beforeSweep = await snapshotTables()
    await byId('hub-improve-fmea').click()
    await byId('hub-improve-fmea-started').waitFor({ state: 'visible', timeout: 30_000 })
    const until = Date.now() + 10 * 60_000
    let sweep = null
    await sleep(1000)
    while (Date.now() < until) {
      sweep = await api('GET', '/api/results/fmea_sweep')
      if (sweep && sweep.status !== 'running') break
      await sleep(1000)
    }
    check(sweep?.status === 'done', `sweep done (base_restored=${sweep?.base_restored})`)
    await byId('hub-improve-open-fmea').click()
    await byId('results-tab-fmea').waitFor({ state: 'visible', timeout: 30_000 })
    await page.waitForFunction(() =>
      [...document.querySelectorAll('[data-testid="fmea-table"] tbody tr')]
        .some(tr => tr.querySelectorAll('td')[1]?.textContent?.trim() === 'B'),
    null, { timeout: 30_000 })
    check((await byId('results-tab-fmea').getAttribute('class')).includes('border-accent'), 'FMEA tab active with class-B rows')
    const sweepDiffs = tableDiffs(beforeSweep, await snapshotTables())
    check(sweepDiffs.diffs.length === 0, `buses/links/generators equal after the sweep (*_nom_opt changes: ${sweepDiffs.nomOpt})`)
    await shot(page, 'p24-dc-fmea-tab')

    // ── tour ──────────────────────────────────────────────────────────────
    step('hub_design tour after a study: walked from Results, then from Improve')
    await byId('sidebar-hub-design').click()
    await byId('hub-rail-step-results').click()
    await byId('hub-card-results').waitFor({ state: 'visible', timeout: 15_000 })
    await walkTour([...PRE_STUDY_TOUR, 'hub-results-verdict'])
    await byId('hub-rail-step-improve').click()
    await byId('hub-card-improve').waitFor({ state: 'visible', timeout: 15_000 })
    await walkTour([...PRE_STUDY_TOUR, 'hub-improve-list', 'hub-improve-fmea'])
    await shot(page, 'p24-dc-after-tour')

    // ── other verdict paths: the H2 hub (no goal) from the Start card ─────
    step('Start card → Industrial Hydrogen Hub (strong grid, no goal)')
    await byId('hub-rail-step-start').click()
    await byId('hub-start-template-eh_h2_hub').click()
    await page.waitForFunction(n =>
      (document.querySelector('[data-testid="hub-start-provenance"]')?.textContent ?? '').includes(n)
      || document.querySelector('[data-testid="hub-card-site"]') !== null, TEMPLATE_NAMES.eh_h2_hub, { timeout: 60_000 })
    await waitRail('site', 'current', 60_000)
    check(await railState('results') === 'blocked', 'new project: Results blocked again')
    await checkSite('eh_h2_hub', 'strong_grid')
    await shot(page, 'p24-h2-site')
    await runStudy('h2', { expectLole: '' , tourWhileRunning: true })
    check(verdicts.h2.headline === 'No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict.',
      'H2 hub (no goal, no shortfall number): the decided §5.5 sentence')

    // ── off-grid wording: the island microgrid from the Start card ────────
    step('Start card → Island Microgrid (off-grid wording, its own verdict)')
    await byId('hub-rail-step-start').click()
    await byId('hub-start-template-eh_microgrid').click()
    await waitRail('site', 'current', 60_000)
    await page.waitForFunction(() =>
      (document.querySelector('[data-testid="hub-site-type"]')?.value) === 'off_grid', null, { timeout: 60_000 })
    await checkSite('eh_microgrid', 'off_grid')
    await shot(page, 'p24-mg-site-offgrid')
    await runStudy('mg', { expectLole: '3' })

    info(`verdicts: ${JSON.stringify(verdicts)}`)

    // ── re-gate B4: the first eh_study read fails ─────────────────────────
    step('B4: eh_study answers 500 on a freshly opened project → error line + Retry, bounded requests, no toasts')
    const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    try {
      const p2 = await ctx2.newPage()
      p2.on('console', m => consoleLines.push(`[b4:${m.type()}] ${m.text()}`))
      const by2 = id => p2.locator(`[data-testid="${id}"]`)
      let n = 0
      const fail = r => { n++; return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }) }
      await p2.route('**/api/results/eh_study', fail)
      await p2.goto(`${WEB}/app?project=${encodeURIComponent(TEMPLATE_NAMES.eh_datacenter)}`, { waitUntil: 'domcontentloaded' })
      await by2('hub-load-error').waitFor({ state: 'visible', timeout: 60_000 })
      check(/could not be read/.test(await by2('hub-load-error').textContent()), 'plain error line shown')
      await by2('hub-card-site').waitFor({ state: 'visible', timeout: 15_000 })
      ok('the Site card renders under the error line (not "Loading the project…")')
      const n0 = n
      await sleep(10_000)
      const burst = n - n0
      check(burst <= 2, `eh_study requests in the 10 s after the error: ${burst} (total ${n})`)
      check((await p2.getByText('Loading the project…').count()) === 0, 'no "Loading the project…"')
      const toasts = await p2.locator('[role="status"]').filter({ hasText: 'boom' }).count()
      check(toasts === 0, `error toasts for the failed read: ${toasts}`)
      await shot(p2, 'p24-b4-study-500-error-line')
      await p2.unroute('**/api/results/eh_study', fail)
      await by2('hub-load-retry').click()
      await by2('hub-load-error').waitFor({ state: 'detached', timeout: 30_000 })
      await by2('hub-card-site').waitFor({ state: 'visible', timeout: 15_000 })
      ok('Retry recovers once the 500 is lifted: error line gone, Site card shown')
      const n1 = n
      await sleep(4_000)
      check(n === n1, 'no further failing requests after recovery')
      await shot(p2, 'p24-b4-after-retry')
    } finally {
      await ctx2.close()
    }
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

// ── the P25 path (spec §6, §8.4; plan P25 row 5) ────────────────────────────
async function phaseP25(browser) {
  const consoleLines = []
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.on('console', m => consoleLines.push(`[${m.type()}] ${m.text()}`))
  page.on('pageerror', e => consoleLines.push(`[pageerror] ${e.message}`))
  const byId = id => page.locator(`[data-testid="${id}"]`)
  const textOf = async id => ((await byId(id).textContent()) ?? '').trim()
  // Every chat request body the page sends, in order.
  const streams = []
  page.on('request', r => {
    if (r.method() === 'POST' && r.url().endsWith('/api/chat/stream')) {
      try { streams.push(JSON.parse(r.postData() ?? '{}')) } catch { streams.push({}) }
    }
  })
  const userMessages = () => page.$$eval('[data-testid="chat-message"][data-role="user"]',
    els => els.map(e => (e.textContent ?? '').trim()))
  const waitUserMessage = (w, timeout = 30_000) => page.waitForFunction(x =>
    [...document.querySelectorAll('[data-testid="chat-message"][data-role="user"]')]
      .some(m => (m.textContent ?? '').includes(x)), w, { timeout })
  const waitIdle = (timeout = 60_000) => page.waitForFunction(() =>
    !document.querySelector('[data-testid="chat-abort"]'), null, { timeout })
  const lastStubText = async () => {
    const rec = await stubRequests()
    return rec.slice(stubSeenBase).at(-1)?.last_user_text ?? ''
  }
  const REPLY_DELAY_MS = 4000

  try {
    step(`stub model profile, replies after ${REPLY_DELAY_MS} ms (so a turn is visibly streaming)`)
    await activateStubProfile({ replyDelayMs: REPLY_DELAY_MS })

    step('fresh profile → Guided; data center template → hub design')
    await page.goto(`${WEB}/projects`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: /From template/ }).first().waitFor({ timeout: 30_000 })
    check(await page.evaluate(k => localStorage.getItem(k), MODE_KEY) === 'guided', 'first-time user is Guided')
    await page.getByRole('button', { name: /From template/ }).first().click()
    await byId('new-project-wizard').waitFor({ state: 'visible', timeout: 15_000 })
    await page.getByRole('button', { name: new RegExp(TEMPLATE_NAMES.eh_datacenter) }).click()
    await page.waitForURL(/\/app\?project=/, { timeout: 60_000 })
    await byId('hub-card-site').waitFor({ state: 'visible', timeout: 60_000 })
    const tagsBefore = { buses: await api('GET', '/api/network/buses'), links: await api('GET', '/api/network/links') }

    step('Goal → Run → done → Results')
    await byId('hub-rail-step-goal').click()
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="hub-goal-lole"]')?.value === '3', null, { timeout: 30_000 })
    await page.waitForFunction(() => !document.querySelector('[data-testid="hub-goal-run"]')?.disabled,
      null, { timeout: 30_000 })
    const before = await snapshotTables()
    await byId('hub-goal-run').click()
    await byId('hub-goal-running').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('hub-card-results').waitFor({ state: 'visible', timeout: 10 * 60_000 })
    const study = await api('GET', '/api/results/eh_study')
    check(study?.status === 'done', 'study done')
    const diffs = tableDiffs(before, await snapshotTables())
    check(diffs.diffs.length === 0, `buses/links/generators equal after the study (*_nom_opt changes: ${diffs.nomOpt})`)
    const review = await api('GET', '/api/results/eh_review')

    step('Improve → "Let the assistant do this" (double click) → ONE user message, Guided context, no attachments')
    await byId('hub-rail-step-improve').click()
    await byId('hub-improve-list').waitFor({ state: 'visible', timeout: 15_000 })
    const f = review.findings.find(x => (x.severity === 'high' || x.severity === 'medium') && x.actions?.length)
    check(!!f, `a high/medium finding with an action: ${f?.id} → ${f?.actions?.[0]?.tool}`)
    const a = f.actions[0]
    const doText = `Apply this recommendation from the study review: "${f.title}". Run the tool ${a.tool} with exactly these arguments: ${JSON.stringify(a.args)}. Say in one sentence what will change, then proceed to the confirmation.`
    check((await byId(`hub-improve-do-${f.id}`).getAttribute('title')).startsWith('Sends this request to the assistant — your attached files are not included.'),
      'the button title says it sends, without attachments')
    const n0 = streams.length
    await byId(`hub-improve-do-${f.id}`).click()
    await byId(`hub-improve-do-${f.id}`).click()          // a double click is deduped
    await waitUserMessage(`Run the tool ${a.tool} with exactly these arguments`)
    await byId('chat-abort').waitFor({ state: 'visible', timeout: 5_000 })
    ok('the request is in the transcript as the user\'s message and the turn is streaming')
    // P25 gate B2: the bubble shows the plain label; the sent text is in a
    // collapsed Details; nothing scrolls sideways.
    const bubble = page.locator('[data-testid="chat-message"][data-role="user"]').last()
    const labelText = ((await bubble.locator('[data-testid="chat-message-label"]').textContent()) ?? '').trim()
    check(labelText.startsWith('Apply this recommendation: ') && !labelText.includes('Run the tool'),
      `bubble label: "${labelText}"`)
    check(await bubble.locator('details[data-testid="chat-message-details"]').evaluate(d => !d.open),
      'the sent text is in a collapsed Details')
    const overflow = await byId('chat-messages').evaluate(e => [e.scrollWidth, e.clientWidth])
    check(overflow[0] <= overflow[1], `transcript does not scroll sideways (scrollWidth ${overflow[0]} ≤ clientWidth ${overflow[1]})`)
    await shot(page, 'p25-improve-do-sent-streaming')

    step('a second card click while the turn streams is queued, not sent')
    await byId('hub-delegate-improve').click()
    const footerText = 'Apply the highest-severity recommendation from review_eh_study, one confirmation at a time, then review again.'
    await sleep(500)
    check(streams.length === n0 + 1, `one stream request so far (${streams.length - n0}); the double click was deduped`)
    check(!(await userMessages()).some(m => m.includes(footerText)), 'the second request is not in the transcript yet')
    const sent = streams[n0]
    check(sent.message === doText, 'stream body message === the §5.7 text')
    check(sent.attachment_file_ids === undefined, 'no attachment_file_ids')
    check(sent.ui_context?.ui_mode === 'guided' && sent.ui_context?.guided_step === 'improve'
      && sent.ui_context?.panel === 'hubDesign',
      `ui_context: ${JSON.stringify(sent.ui_context)}`)
    check(sent.input_mode === 'text', 'input_mode text')
    await shot(page, 'p25-second-click-queued')

    step('the stub\'s scripted tool call (§6.6) → confirmation card; the stub saw the Guided addendum')
    await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 60_000 })
    const card = await textOf('chat-confirmation-card')
    check(card.includes(a.tool), `confirmation card for ${a.tool}`)
    const recorded = await lastStubText()
    check(recorded.includes('Guided mode is on') && recorded.includes('"Improve" card'),
      'stub\'s recorded last user text contains "Guided mode is on" and the Improve card')
    check(recorded.trimEnd().endsWith(doText), 'the user\'s own words come last')
    check(streams.length === n0 + 1, 'the queued request still waits while the card is pending')
    await shot(page, 'p25-confirmation-card')

    step('decline → the turn ends → the queued request is sent')
    const beforeDecline = await snapshotTables()
    await byId('chat-confirm-deny').click()
    await byId('chat-confirmation-card').waitFor({ state: 'detached', timeout: 30_000 })
    await waitUserMessage(footerText, 60_000)
    check(streams.length === n0 + 2 && streams[n0 + 1].message === footerText,
      'the queued request was sent after the first turn ended, in order')
    await waitIdle()
    const declined = tableDiffs(beforeDecline, await snapshotTables())
    check(declined.diffs.length === 0, 'nothing changed on the network after the decline')
    const all = await userMessages()
    check(all.filter(m => m.includes(`Run the tool ${a.tool}`)).length === 1, 'the action request appears exactly once')
    await shot(page, 'p25-queued-sent-after-decline')

    step('Expert: a typed message carries no ui_mode and the stub sees no addendum')
    await byId('ui-mode-expert').click()
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="ui-mode-expert"]')?.getAttribute('aria-pressed') === 'true',
    null, { timeout: 15_000 })
    // The switch's toast sits over the Send button until it fades.
    await page.getByText('Expert mode on').waitFor({ state: 'detached', timeout: 15_000 }).catch(() => {})
    const n2 = streams.length
    await byId('chat-input').fill('What does the study say?')
    await byId('chat-send').click()
    await waitUserMessage('What does the study say?')
    await waitIdle()
    const expertBody = streams[n2]
    check(!!expertBody && !('ui_mode' in (expertBody.ui_context ?? {}))
      && !('guided_step' in (expertBody.ui_context ?? {})),
      `Expert body has no ui_mode / guided_step (ui_context: ${JSON.stringify(expertBody?.ui_context ?? null)})`)
    const expertText = await lastStubText()
    check(expertText.includes('What does the study say?') && !expertText.includes('Guided mode is on')
      && !expertText.includes('mode: guided'), 'stub\'s recorded text has no Guided addendum')
    await shot(page, 'p25-expert-no-addendum')

    step('suggest_eh_setup on the template with its tags stripped: recovers them, writes nothing')
    const want = {
      import: tagsBefore.links.filter(l => l.eh_role === 'grid_import').map(l => l.name),
      poc: tagsBefore.buses.filter(b => b.eh_poc === true).map(b => b.name),
      critical: tagsBefore.buses.filter(b => b.eh_critical === true).map(b => b.name),
    }
    check(want.import.length && want.poc.length && want.critical.length,
      `template tags: ${JSON.stringify(want)}`)
    await api('PATCH', '/api/network/_bulk', { component_class: 'Bus',
      names: tagsBefore.buses.map(b => b.name), updates: { eh_poc: false, eh_critical: false } })
    await api('PATCH', '/api/network/_bulk', { component_class: 'Link',
      names: tagsBefore.links.map(l => l.name), updates: { eh_role: '' } })
    const stripped = await snapshotTables()
    check(!stripped.buses.some(b => b.eh_poc || b.eh_critical) && !stripped.links.some(l => l.eh_role),
      'tags stripped')
    const n3 = (await stubRequests()).length
    await byId('chat-input').fill('Run the tool suggest_eh_setup with exactly these arguments: {"archetype":"weak_flexible"}')
    await byId('chat-send').click()
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="chat-message"]')]
      .some(m => (m.textContent ?? '').includes('Done — suggest_eh_setup finished.')), null, { timeout: 60_000 })
    await waitIdle()
    check((await byId('chat-confirmation-card').count()) === 0, 'a read tool: no confirmation card')
    const rec = (await stubRequests()).slice(n3)
    const toolMsg = rec.flatMap(r => r.payload.messages ?? []).filter(m => m.role === 'tool').at(-1)
    const body = String(toolMsg?.content ?? '')
    const jsonStart = body.indexOf('{')
    const result = JSON.parse(body.slice(jsonStart, body.lastIndexOf('}') + 1))
    const got = kind => result.suggestions.filter(s => s.kind === kind).map(s => s.name).sort()
    check(JSON.stringify(got('import_link')) === JSON.stringify([...want.import].sort()), `import link: ${got('import_link')}`)
    check(JSON.stringify(got('poc_bus')) === JSON.stringify([...want.poc].sort()), `PoC bus: ${got('poc_bus')}`)
    check(JSON.stringify(got('critical_bus')) === JSON.stringify([...want.critical].sort()), `critical: ${got('critical_bus')}`)
    info(`actions: ${result.actions.map(x => `${x.tool}(${x.args.name ?? x.args.names})`).join(', ')}`)
    const afterSuggest = tableDiffs(stripped, await snapshotTables())
    check(afterSuggest.diffs.length === 0, 'suggest_eh_setup wrote nothing (tables equal)')
    await shot(page, 'p25-suggest-eh-setup')

    step('B2: the Details expand to the exact text that was sent')
    const improveBubble = page.locator('[data-testid="chat-message"][data-role="user"]')
      .filter({ has: page.locator('[data-testid="chat-message-label"]') }).first()
    await improveBubble.scrollIntoViewIfNeeded()
    await improveBubble.locator('summary').click()
    const sentText = ((await improveBubble.locator('[data-testid="chat-message-text"]').textContent()) ?? '').trim()
    check(sentText === doText, 'expanded Details show the sent §5.7 text verbatim')
    const ow = await byId('chat-messages').evaluate(e => [e.scrollWidth, e.clientWidth])
    check(ow[0] <= ow[1], `expanded: still no sideways scroll (${ow[0]} ≤ ${ow[1]})`)
    await shot(page, 'p25-bubble-details-expanded')

    step('B1: a Site fix in Guided (update_component, write tier) stops at a confirmation card')
    await byId('ui-mode-guided').click()
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="ui-mode-guided"]')?.getAttribute('aria-pressed') === 'true',
    null, { timeout: 15_000 })
    await page.getByText('Guided mode on').waitFor({ state: 'detached', timeout: 15_000 }).catch(() => {})
    if (!(await byId('hub-design-panel').isVisible().catch(() => false))) await byId('sidebar-hub-design').click()
    await byId('hub-rail-step-site').click()
    // (readiness still finds the grid link through its own selection rule
    // once the tags are gone, so the critical-load fix is the one offered)
    await byId('hub-site-fix-critical').waitFor({ state: 'visible', timeout: 60_000 })
    const beforeFix = await snapshotTables()
    const n4 = streams.length
    await byId('hub-site-fix-critical').click()
    await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 60_000 })
    check(await byId('chat-confirmation-card').getAttribute('data-tool-name') === 'update_component'
      && await byId('chat-confirmation-card').getAttribute('data-safety-tier') === 'write',
      'card for update_component, tier write')
    check(streams[n4]?.ui_context?.ui_mode === 'guided' && streams[n4]?.ui_context?.guided_step === 'site',
      'sent from the Site card in Guided')
    const whilePending = tableDiffs(beforeFix, await snapshotTables())
    check(whilePending.diffs.length === 0, 'nothing changed while the card waits')
    await byId('chat-confirmation-card').scrollIntoViewIfNeeded()
    await shot(page, 'p25-site-fix-card-guided')
    await byId('chat-confirm-deny').click()
    await byId('chat-confirmation-card').waitFor({ state: 'detached', timeout: 30_000 })
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="chat-message"]')]
      .some(m => (m.textContent ?? '').includes('Understood — update_component was not applied.')), null, { timeout: 60_000 })
    await waitIdle()
    const afterDeny = tableDiffs(beforeFix, await snapshotTables())
    check(afterDeny.diffs.length === 0, 'denied: nothing changed')

    step('R1: several writes in ONE response — deny card 1, card 2 is still shown (5 rounds + 1 approve round)')
    // The Site footer asks to fix every gap; the stub (branch 4) reads
    // suggest_eh_setup and returns ALL its actions in one response, so the
    // backend cards them one after the other and the next card's frame can
    // land before the previous /confirm returns (re-gate R1).
    const cardArgs = async () => ((await byId('chat-confirmation-card').locator('pre').textContent()) ?? '').trim()
    const nextCard = prev => page.waitForFunction(p => {
      const c = document.querySelector('[data-testid="chat-confirmation-card"]')
      return c && (c.querySelector('pre')?.textContent ?? '').trim() !== p
    }, prev, { timeout: 8_000 })
    const beforeR1 = await snapshotTables()
    const answer = async (decision) => {
      const b = byId(decision === 'approve' ? 'chat-confirm-approve' : 'chat-confirm-deny')
      await b.click()
    }
    for (let round = 1; round <= 6; round++) {
      const approveFirst = round === 6
      await byId('hub-delegate-site').click()
      await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 60_000 })
      const seen = []
      let args = await cardArgs()
      seen.push(args)
      for (let k = 0; k < 10; k++) {
        await answer(approveFirst && k === 0 ? 'approve' : 'deny')
        const more = await nextCard(args).then(() => true, () => false)
        if (!more) break
        // The bug wiped the next card a moment after it rendered: look again.
        await sleep(600)
        check(await byId('chat-confirmation-card').isVisible(),
          `round ${round}: card ${seen.length + 1} still shown after answering card ${seen.length}`)
        args = await cardArgs()
        seen.push(args)
        if (round === 1 && k === 0) {
          await byId('chat-confirmation-card').scrollIntoViewIfNeeded()
          await shot(page, 'p25-r1-card2-after-deny')
        }
      }
      await waitIdle()
      check(seen.length >= 2, `round ${round}: ${seen.length} cards in one response, each shown after the previous answer (${approveFirst ? 'approve' : 'deny'} first)`)
      check((await byId('chat-confirmation-card').count()) === 0, `round ${round}: no card left, turn ended`)
      if (!approveFirst) {
        const d = tableDiffs(beforeR1, await snapshotTables())
        check(d.diffs.length === 0, `round ${round}: all denied → nothing changed`)
      }
    }

    step('note 3: the Goal VOLL button → update_solver_config card; VOLL unchanged until Approve')
    await api('PUT', '/api/simulation/solver_config', { voll: 0 })
    await byId('hub-rail-step-goal').click()
    await byId('hub-goal-voll-fix').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('hub-goal-voll-fix').click()
    await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 60_000 })
    check(await byId('chat-confirmation-card').getAttribute('data-tool-name') === 'update_solver_config',
      'card for update_solver_config (from the Goal button itself)')
    check((await textOf('chat-confirmation-header')) === 'Confirm this change', 'Guided header: "Confirm this change"')
    check((await api('GET', '/api/simulation/solver_config')).voll === 0, 'VOLL still 0 while the card waits')
    await byId('chat-confirmation-card').scrollIntoViewIfNeeded()
    await shot(page, 'p25-goal-voll-card')
    await byId('chat-confirm-approve').click()
    await waitIdle()
    check((await api('GET', '/api/simulation/solver_config')).voll === 5000, 'approved → VOLL 5000')
    await page.waitForFunction(() => /€5,000 per MWh/.test(
      document.querySelector('[data-testid="hub-goal-voll"]')?.textContent ?? ''), null, { timeout: 30_000 })
    ok('the Goal card shows €5,000 per MWh')

    step('note 3: Improve "Add a stress scenario" → read, then a put_stress_scenarios card; registry unchanged until Approve')
    const proj = encodeURIComponent(TEMPLATE_NAMES.eh_datacenter)
    const regBefore = (await api('GET', `/api/projects/${proj}/stress_scenarios`)).scenarios
    await byId('hub-rail-step-improve').click()
    await byId('hub-improve-add-stress').waitFor({ state: 'visible', timeout: 30_000 })
    await byId('hub-improve-add-stress').click()
    await byId('chat-confirmation-card').waitFor({ state: 'visible', timeout: 60_000 })
    check(await byId('chat-confirmation-card').getAttribute('data-tool-name') === 'put_stress_scenarios',
      'card for put_stress_scenarios (from the Improve button itself)')
    const regPending = (await api('GET', `/api/projects/${proj}/stress_scenarios`)).scenarios
    check(JSON.stringify(regPending) === JSON.stringify(regBefore), 'registry unchanged while the card waits')
    await byId('chat-confirmation-card').scrollIntoViewIfNeeded()
    await shot(page, 'p25-stress-card')
    await byId('chat-confirm-approve').click()
    await waitIdle()
    const regAfter = (await api('GET', `/api/projects/${proj}/stress_scenarios`)).scenarios
    check(regAfter.length === regBefore.length + 1
      && JSON.stringify(regAfter.slice(0, regBefore.length)) === JSON.stringify(regBefore),
      `approved → the whole list kept plus one (${regBefore.length} → ${regAfter.length})`)

    step('B1: the same Site fix in Expert applies directly (no card), as before P25')
    await byId('ui-mode-expert').click()
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="ui-mode-expert"]')?.getAttribute('aria-pressed') === 'true',
    null, { timeout: 15_000 })
    await page.getByText('Expert mode on').waitFor({ state: 'detached', timeout: 15_000 }).catch(() => {})
    const siteFix = 'On the Site card, no critical load is tagged. Propose which buses must stay on (eh_critical = true) and tag them after I confirm.'
    await byId('chat-input').fill(siteFix)
    await byId('chat-send').click()
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="chat-message"]')]
      .some(m => (m.textContent ?? '').includes('Done — update_component applied.')), null, { timeout: 60_000 })
    await waitIdle()
    check((await byId('chat-confirmation-card').count()) === 0, 'Expert: no confirmation card for the write')
    const buses = await api('GET', '/api/network/buses')
    check(buses.find(b => b.name === want.critical[0])?.eh_critical === true,
      `Expert: ${want.critical[0]} tagged eh_critical directly`)
    await shot(page, 'p25-site-fix-expert-direct')
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
  else if (args.phase === 'P24-BE') await phaseP229(browser, { reviewChecks: true })
  else if (args.phase === 'P23') await phaseP23(browser)
  else if (args.phase === 'P24') await phaseP24(browser)
  else if (args.phase === 'P25') await phaseP25(browser)
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
