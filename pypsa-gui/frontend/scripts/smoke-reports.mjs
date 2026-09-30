#!/usr/bin/env node
/**
 * Live smoke for phase 3 of the study-report plan (WP7b):
 *   sign in → load a network → save a project → run a small Energy Hub study
 *   → evidence-only report → generate (LLM) → poll → viewer document
 *   → regenerate one section → export .docx → the blob lands (`PK…`)
 *   → with --browser, the SPA's Reports panel lists it and the viewer shows
 *     its sections (headless Chromium through Playwright).
 *
 * Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
 * (phase-3 row: `smoke-reports.mjs`). PASS/FAIL lines like the backend QA
 * drivers (`backend/tests/qa_reports_phase1.py`); exit 1 on any FAIL.
 *
 * Run it against a backend that is already up:
 *
 *   # the repo's launcher (backend on :8000, Vite on :5173):
 *   ./pypsa-gui/start.sh
 *   node scripts/smoke-reports.mjs --base http://127.0.0.1:8000
 *
 *   # or uvicorn alone, local desktop mode (no login), from pypsa-gui/backend
 *   # with the dev env the README's "Local desktop mode" table lists:
 *   PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_PROJECTS_ROOT=/tmp/smoke/projects \
 *   PYPSAGUI_APP_DATA_DIR=/tmp/smoke/appdata DATABASE_URL=sqlite:////tmp/smoke/appdata/auth.db \
 *   CORS_ALLOWED_ORIGINS=http://127.0.0.1:8765 MPLBACKEND=Agg \
 *   python -m uvicorn main:app --port 8765
 *   node scripts/smoke-reports.mjs --base http://127.0.0.1:8765 [--browser]
 *
 * Options / environment
 *   --base URL          backend origin (default http://127.0.0.1:8000)
 *   --browser           also drive the built SPA (`npm run build` first when
 *                       the backend serves it; in local mode /app is the app)
 *   --network FILE.nc   the network to load (or SMOKE_NETWORK_NC). Without
 *                       one, the script builds the certifiable weak hub from
 *                       backend/tests/eh_stage_fixtures.py with SMOKE_PYTHON
 *                       (default `python`, which must import pypsa).
 *   --project NAME      project name (default smoke_reports)
 *   --keep              do not delete the project's reports at the end
 *   SMOKE_EMAIL / SMOKE_PASSWORD   the sign-in when /api/health says
 *                       auth_enabled (defaults: admin@example.com /
 *                       admin-pass-123, as scripts/smoke-auth-gate.mjs)
 *   PLAYWRIGHT_BROWSERS_PATH       honoured by Playwright when --browser
 *
 * Generation needs a usable LLM profile on the backend (an API key or a local
 * model). Without one the generate route answers 400 `missing_api_key` /
 * `sdk_not_installed`; that is an EXPECTED, reported outcome here — the
 * script says so and continues on the evidence-only report. The summary line
 * names which path ran.
 *
 * No new dependency: Node's built-in fetch/FormData/Blob drive the API.
 * Playwright is loaded only with --browser, from the project's node_modules
 * when it is there, else from the global install (`npm root -g`).
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// ── args ────────────────────────────────────────────────────────────────────

const argv = process.argv.slice(2)
function flag(name) { return argv.includes(name) }
function opt(name, fallback) {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}
const BASE = opt('--base', process.env.SMOKE_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')
const BROWSER = flag('--browser')
const KEEP = flag('--keep')
const PROJECT = opt('--project', 'smoke_reports')
const NETWORK = opt('--network', process.env.SMOKE_NETWORK_NC || '')
const PYTHON = process.env.SMOKE_PYTHON || 'python'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const BACKEND_DIR = path.resolve(HERE, '..', '..', 'backend')

const STUDY = { archetype: 'strong_grid', budget_solves: 8, stages: ['apply_pack', 'ens_solve', 'fmea_top', 'assemble'] }
const VOLL = 150.0

// ── PASS / FAIL ledger ──────────────────────────────────────────────────────

let PASS = 0
let FAIL = 0
function step(label, ok, msg = '') {
  if (ok) { PASS += 1; console.log(`  [PASS] ${label}${msg ? ` — ${msg}` : ''}`) }
  else { FAIL += 1; console.log(`  [FAIL] ${label}${msg ? ` — ${msg}` : ''}`) }
  return ok
}
function note(msg) { console.log(`  [....] ${msg}`) }
function section(title) { console.log(`\n[${title}]`) }

// ── HTTP ────────────────────────────────────────────────────────────────────

let cookieHeader = ''
let csrfToken = ''
const cookieJar = []

function absorbCookies(res) {
  const set = res.headers.getSetCookie?.() || []
  for (const c of set) {
    const [pair] = c.split(';')
    const eq = pair.indexOf('=')
    if (eq < 0) continue
    const name = pair.slice(0, eq).trim()
    const value = pair.slice(eq + 1).trim()
    const i = cookieJar.findIndex(k => k.name === name)
    if (i >= 0) cookieJar[i] = { name, value }
    else cookieJar.push({ name, value })
    if (name === 'pypsa_gui_csrf') csrfToken = decodeURIComponent(value)
  }
  cookieHeader = cookieJar.map(k => `${k.name}=${k.value}`).join('; ')
}

async function api(pathname, { method = 'GET', json, form, raw = false, headers = {} } = {}) {
  const h = { Accept: 'application/json', ...headers }
  if (cookieHeader) h.Cookie = cookieHeader
  if (method !== 'GET' && method !== 'HEAD' && csrfToken) h['X-CSRF-Token'] = csrfToken
  let body
  if (json !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(json) }
  if (form) body = form
  const res = await fetch(`${BASE}${pathname}`, { method, headers: h, body, redirect: 'manual' })
  absorbCookies(res)
  if (raw) return { res, bytes: Buffer.from(await res.arrayBuffer()) }
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  return { res, data, text }
}

function errorKind(data) {
  const d = data && typeof data === 'object' ? data.detail : null
  return d && typeof d === 'object' ? d.error_kind : null
}
function short(x, n = 240) { return String(typeof x === 'string' ? x : JSON.stringify(x)).slice(0, n) }

async function poll(pathname, { timeoutMs = 600_000, everyMs = 500 } = {}) {
  const t0 = Date.now()
  let last = null
  while (Date.now() - t0 < timeoutMs) {
    const { res, data } = await api(pathname)
    if (res.status === 204) return { status: 'no-content' }
    if (res.status !== 200) return { status: `http-${res.status}`, error: short(data) }
    last = data
    if (data.status !== 'running') return data
    await new Promise(r => setTimeout(r, everyMs))
  }
  return { status: 'timeout', ...(last || {}) }
}

// ── the network to load ─────────────────────────────────────────────────────

function fixtureNetwork() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'smoke-reports-')), 'network.nc')
  const code = [
    'import sys', `sys.path.insert(0, ${JSON.stringify(BACKEND_DIR)})`,
    'from tests.eh_stage_fixtures import certifiable_weak_network',
    `certifiable_weak_network().export_to_netcdf(${JSON.stringify(out)})`,
  ].join('\n')
  execFileSync(PYTHON, ['-c', code], { stdio: ['ignore', 'ignore', 'inherit'], env: { ...process.env, MPLBACKEND: 'Agg' } })
  return out
}

// ── the journey ─────────────────────────────────────────────────────────────

let authEnabled = false

async function signIn() {
  section('0 sign in')
  const { res, data } = await api('/api/health')
  if (!step('GET /api/health answers', res.status === 200, short(data))) return false
  authEnabled = Boolean(data.auth_enabled)
  if (!authEnabled) { note('auth_enabled=false (local mode): no login, no CSRF'); return true }
  const login = await api('/api/auth/login', {
    method: 'POST',
    json: { email: process.env.SMOKE_EMAIL || 'admin@example.com', password: process.env.SMOKE_PASSWORD || 'admin-pass-123' },
  })
  step('POST /api/auth/login', login.res.ok, short(login.data))
  step('the login set a session cookie and a CSRF token', Boolean(cookieHeader) && Boolean(csrfToken))
  return login.res.ok
}

async function loadAndStudy() {
  section('1 network → project → strong_grid study')
  const nc = NETWORK || fixtureNetwork()
  step('a network file exists', fs.existsSync(nc), nc)
  const form = new FormData()
  form.append('file', new Blob([fs.readFileSync(nc)]), 'network.nc')
  const imp = await api('/api/io/import/netcdf', { method: 'POST', form })
  if (!step('POST /api/io/import/netcdf', imp.res.status === 200, short(imp.data))) return false
  const save = await api(`/api/projects/${encodeURIComponent(PROJECT)}?force=true&clear_undo=false`, { method: 'POST' })
  if (!step(`POST /api/projects/${PROJECT} (save)`, save.res.status === 200, short(save.data))) return false
  const cfg = await api('/api/simulation/solver_config', { method: 'PUT', json: { solver_name: 'highs', voll: VOLL } })
  step('PUT /api/simulation/solver_config takes the VoLL', cfg.res.status === 200, short(cfg.data))
  const start = await api('/api/results/eh_study', { method: 'POST', json: STUDY })
  if (!step('POST /api/results/eh_study starts', start.res.status === 200, short(start.data))) return false
  const study = await poll('/api/results/eh_study')
  if (!step('the study finishes', study.status === 'done', `status=${study.status} error=${short(study.error)}`)) return false
  const rep = await api('/api/results/eh_reference_design')
  step('GET /api/results/eh_reference_design', rep.res.status === 200)
  const comp = (rep.data && rep.data.completeness) || {}
  step('fmea_top is established', comp.fmea_top === 'ok', short(comp))
  return true
}

const R = (p = '') => `/api/projects/${encodeURIComponent(PROJECT)}/reports${p}`

async function evidenceOnlyReport() {
  section('2 POST /reports (evidence_only)')
  const { res, data } = await api(R(), { method: 'POST', json: { mode: 'evidence_only', title: 'Smoke evidence report' } })
  if (!step('the route answers 200', res.status === 200, short(data))) return null
  step('the response carries report_id + document', /^[0-9a-f]{16}$/.test(data.report_id || '') && data.document && data.document.sections.length > 0,
    `report_id=${data.report_id} sections=${data.document && data.document.sections.length}`)
  step('mode is evidence_only', data.mode === 'evidence_only')
  return data
}

/** Returns {path: 'generated'|'skipped', reportId, record} */
async function generate() {
  section('3 POST /reports/generate → poll → regenerate')
  const start = await api(R('/generate'), { method: 'POST', json: { title: 'Smoke generated report', language: 'en' } })
  const kind = errorKind(start.data)
  if (start.res.status === 400 && (kind === 'missing_api_key' || kind === 'sdk_not_installed')) {
    step(`generate refused as ${kind} (no usable LLM profile) — expected without a key`, true, short(start.data && start.data.detail && start.data.detail.message))
    note('GENERATION PATH: skipped — continuing with the evidence-only report')
    return { path: 'skipped', reportId: null, record: null }
  }
  if (!step('POST …/generate answers 200 {status: running, report_id}', start.res.status === 200 && start.data.status === 'running', short(start.data))) {
    return { path: 'failed', reportId: null, record: null }
  }
  const reportId = start.data.report_id
  const record = await poll(R('/generate/status'), { timeoutMs: 900_000, everyMs: 1500 })
  step('the job leaves running', ['done', 'aborted', 'failed'].includes(record.status), `status=${record.status}`)
  step('the job is done', record.status === 'done', `error=${short(record.error)} progress=${short(record.progress)}`)
  step('the record names the writer', Boolean(record.profile_id) || Boolean(record.model), `${record.profile_id} · ${record.model}`)
  note(`repairs=${record.repairs} prose_failures=${(record.prose_failures || []).length} version=${record.version}`)
  if (record.status !== 'done') return { path: 'failed', reportId, record }

  const doc = await api(R(`/${reportId}`))
  step('GET …/reports/{id} serves the generated document', doc.res.status === 200 && doc.data.mode === 'generated', `mode=${doc.data && doc.data.mode}`)
  const llmSections = (doc.data.sections || []).filter(s => s.source === 'llm')
  step('at least one section carries model prose', llmSections.length > 0, `llm sections=${llmSections.length}`)
  const flagged = (doc.data.sections || []).reduce((n, s) => n + ((s.audit && s.audit.unverified) || []).length, 0)
  note(`numbers flagged as not in the evidence: ${flagged}`)

  const target = (doc.data.sections || [])[0]
  const regen = await api(R(`/${reportId}/sections/${encodeURIComponent(target.section_id)}/regenerate`), {
    method: 'POST', json: { instruction: 'Rewrite in two sentences.' },
  })
  if (step(`POST …/sections/${target.section_id}/regenerate answers 200`, regen.res.status === 200, short(regen.data))) {
    const rec2 = await poll(R('/generate/status'), { timeoutMs: 600_000, everyMs: 1500 })
    step('the regenerate job is done', rec2.status === 'done', `status=${rec2.status} error=${short(rec2.error)}`)
    step('the new version is base + 1', rec2.version === (doc.data.version + 1), `v${doc.data.version} → v${rec2.version}`)
    const doc2 = await api(R(`/${reportId}`))
    step('GET …/reports/{id} now serves the new latest version', doc2.res.status === 200 && doc2.data.version === rec2.version)
  }
  note('GENERATION PATH: generated')
  return { path: 'generated', reportId, record }
}

async function listFetchExport(reportId) {
  section('4 list → fetch → export → blob')
  const list = await api(R())
  step('GET …/reports lists the report', list.res.status === 200 && list.data.some(m => m.report_id === reportId), `n=${Array.isArray(list.data) ? list.data.length : '?'}`)
  const doc = await api(R(`/${reportId}`))
  if (!step('GET …/reports/{id}', doc.res.status === 200, short(doc.data))) return
  step('every section has a heading and a status', doc.data.sections.every(s => s.heading && s.status), `sections=${doc.data.sections.length}`)
  note(`tables=${Object.keys(doc.data.tables || {}).length} figures=${Object.keys(doc.data.figures || {}).length}`)
  const figs = Object.keys(doc.data.figures || {})
  if (figs.length) {
    const png = await api(R(`/${reportId}/figures/${figs[0]}`), { raw: true })
    step('a figure PNG is served', png.res.status === 200 && png.bytes.slice(1, 4).toString() === 'PNG')
  }
  const exp = await api(R(`/${reportId}/export`), { method: 'POST', json: {} })
  if (!step('POST …/export answers with the upload meta', exp.res.status === 200 && exp.data.file_id, short(exp.data))) return
  const blob = await api(`/api/projects/${encodeURIComponent(PROJECT)}/uploads/${exp.data.file_id}/blob`, { raw: true })
  step('GET …/uploads/{file_id}/blob is a .docx (PK…)', blob.res.status === 200 && blob.bytes.slice(0, 2).toString() === 'PK', `bytes=${blob.bytes.length} filename=${exp.data.filename}`)
}

// ── browser (Playwright) ────────────────────────────────────────────────────

async function loadPlaywright() {
  try { return await import('playwright') } catch { /* not in node_modules */ }
  try {
    const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim()
    return await import(pathToFileURL(path.join(root, 'playwright', 'index.mjs')).href)
  } catch (e) {
    throw new Error(`Playwright is not resolvable from node_modules or the global install: ${e.message}`)
  }
}

async function browserCheck(reportId) {
  section('5 --browser: the SPA lists the report and the viewer shows its sections')
  let pw
  try { pw = await loadPlaywright() } catch (e) { step('Playwright loads', false, e.message); return }
  step('Playwright loads', true)
  const browser = await pw.chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ baseURL: BASE })
    if (authEnabled && cookieJar.length) {
      const u = new URL(BASE)
      await context.addCookies(cookieJar.map(k => ({ name: k.name, value: k.value, domain: u.hostname, path: '/' })))
    }
    // A fresh browser profile is a FIRST RUN, which the guided-mode spec
    // (§3.2, G4) starts in Guided mode — and Guided hides the Reports row
    // (Expert-only sidebar, §3.5). This journey is the Expert one, so choose
    // Expert explicitly before the SPA boots, the way a user who picked it
    // in the mode switcher is remembered (`uiStore` UI_MODE_KEY / _EXPLICIT_KEY).
    await context.addInitScript(() => {
      try {
        localStorage.setItem('network-diagram:ui-mode', 'expert')
        localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      } catch { /* storage unavailable: the journey then reports the missing row */ }
    })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(String(e)))
    await page.goto(`/app?project=${encodeURIComponent(PROJECT)}`, { waitUntil: 'domcontentloaded' })
    const reportsItem = page.locator('[title^="Study reports"]').first()
    await reportsItem.waitFor({ timeout: 30_000 })
    step('the sidebar has the Reports row', true)
    await reportsItem.click()
    const rows = page.locator('[data-testid="report-row"]')
    await rows.first().waitFor({ timeout: 30_000 })
    const n = await rows.count()
    step('the panel lists the report', n >= 1, `rows=${n}`)
    const gen = page.locator('[data-testid="reports-generate"]')
    step('the Generate report… button is there', (await gen.count()) === 1)
    // The newest report is listed first; with --keep off it is the one this
    // run made (the id is what the API steps above asserted on).
    note(`opening the first row (report ${reportId})`)
    await rows.first().locator('[data-testid="report-open"]').click()
    const sections = page.locator('[data-testid="report-section"]')
    await sections.first().waitFor({ timeout: 30_000 })
    const ns = await sections.count()
    step('the viewer shows the sections', ns >= 1, `sections=${ns}`)
    const marks = await page.locator('[data-testid="report-section"] mark').count()
    note(`flagged numbers in the viewer: ${marks}`)
    step('the per-section Regenerate… control is offered', (await page.locator('[data-testid="section-regenerate"]').count()) >= 1)
    step('no uncaught page errors', errors.length === 0, errors.join(' | ').slice(0, 300))
  } catch (e) {
    step('browser journey', false, String(e).slice(0, 300))
  } finally {
    await browser.close()
  }
}

// ── main ────────────────────────────────────────────────────────────────────

console.log(`smoke-reports against ${BASE} (project ${PROJECT}${BROWSER ? ', --browser' : ''})`)
let genPath = 'not reached'
try {
  if (await signIn() && await loadAndStudy()) {
    const evidence = await evidenceOnlyReport()
    if (evidence) {
      const gen = await generate()
      genPath = gen.path
      await listFetchExport(gen.reportId || evidence.report_id)
      if (BROWSER) await browserCheck(gen.reportId || evidence.report_id)
      if (!KEEP) {
        section('6 cleanup')
        for (const id of [evidence.report_id, gen.reportId].filter(Boolean)) {
          const del = await api(R(`/${id}`), { method: 'DELETE' })
          step(`DELETE …/reports/${id}`, del.res.status === 200, short(del.data))
        }
      }
    }
  }
} catch (e) {
  step('unexpected error', false, String(e && e.stack || e).slice(0, 500))
}

console.log(`\nGENERATION PATH: ${genPath}`)
console.log(`RESULT: ${PASS} passed, ${FAIL} failed`)
process.exit(FAIL === 0 ? 0 : 1)
