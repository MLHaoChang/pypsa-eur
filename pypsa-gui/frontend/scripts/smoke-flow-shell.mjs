#!/usr/bin/env node
// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Browser journey for the project-flow shell (design §2.3, §3.3, §6): the
 * Guided rail, the Expert sidebar, the Expert / Guided switch, Issues by
 * section and the Run gate, on every example template the app ships.
 *
 *   cd pypsa-gui/frontend
 *   node scripts/smoke-flow-shell.mjs [--template eh_datacenter,3bus,...] [--shots <dir>]
 *
 * Needs the API on :8000 and Vite on :5173 (`pixi run gui`), the dev admin
 * from scripts/dev-credentials.mjs, and Chromium (PLAYWRIGHT_BROWSERS_PATH or
 * CHROMIUM_BIN). Each template is created as a fresh project and opened in a
 * fresh browser context; the oracle is `GET /api/projects/<name>/flow`, so the
 * shell is compared with what the server says, not with a fixture.
 *
 * Per template:
 *  1. Guided: the rail shows ten sections, each with the state /flow reports;
 *     picking each one shows its section view / card with the open items the
 *     one filter (`isOpenItem`) counts.
 *  2. Switch to Expert: the sidebar has the ten sections' state dots, in four
 *     bands plus the project foot, with the same states; the section chosen in
 *     Guided is still the active one.
 *  3. Issues (Expert): every open item sits under its own flow section's
 *     header, and a header's count is its items.
 *  4. Run gate: Expert's Run is disabled exactly when /flow says
 *     `verdicts.optimisation` (else `run_ready`) is false, naming the blocker;
 *     Guided shows the same verdict on the Readiness card.
 *  5. Switch back to Guided: the section is unchanged.
 *  6. Resume: a reload (in either mode) reopens the section it was left in.
 *
 * Screenshots: <shots>/<template>-<step>.jpg (default: none; pass --shots to
 * write them, e.g. docs/superpowers/plans/2026-10-21-flow-shell/shots).
 * Exit 0 when every template passes; 1 with the failing checks listed.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const WEB = process.env.SMOKE_WEB || 'http://127.0.0.1:5173'

/** Every template `POST /api/projects/from_template/<id>` accepts
 *  (`_TEMPLATE_DEFAULT_NAMES` in backend/routers/projects.py). */
const ALL_TEMPLATES = [
  '3bus', 'ieee14', 'belgium', 'ieee39',
  'eh_datacenter', 'eh_datacenter_staged', 'eh_site',
  'eh_h2_hub', 'eh_h2_hub_staged', 'eh_microgrid', 'eh_microgrid_staged',
  'eh_ammonia_staged', 'eh_ipp_hybrid',
]
const SECTIONS = [
  'project', 'carriers', 'connection', 'loads', 'assets', 'profiles',
  'commercial', 'scenarios', 'readiness', 'results',
]
const RAIL_ID = {
  project: 'hub-rail-step-start', carriers: 'hub-rail-section-carriers', connection: 'hub-rail-step-site',
  loads: 'hub-rail-section-loads', assets: 'hub-rail-section-assets', profiles: 'hub-rail-section-profiles',
  commercial: 'hub-rail-section-commercial', scenarios: 'hub-rail-step-goal',
  readiness: 'hub-rail-section-readiness', results: 'hub-rail-step-results',
}
const RAIL_STATE = { complete: 'done', warning: 'warning', incomplete: 'todo', not_needed: 'not_needed', unknown: 'todo' }
const isOpen = i => !i.acknowledged && i.in_scope !== false && i.severity !== 'info'

function parseArgs(argv) {
  const out = { templates: ALL_TEMPLATES, shots: null }
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--template') out.templates = argv[++i].split(',')
    else if (argv[i] === '--shots') out.shots = path.resolve(argv[++i])
    else { console.error(`unknown argument ${argv[i]}`); process.exit(2) }
  }
  for (const t of out.templates) {
    if (!ALL_TEMPLATES.includes(t)) { console.error(`unknown template ${t}`); process.exit(2) }
  }
  if (out.shots) fs.mkdirSync(out.shots, { recursive: true })
  return out
}

async function walk(browser, template, args) {
  const failures = []
  const check = (cond, msg) => { if (!cond) failures.push(msg); else console.log(`    ok  ${msg}`) }
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const shot = async name => {
    if (!args.shots) return
    await page.screenshot({ path: path.join(args.shots, `${template}-${name}.jpg`), type: 'jpeg', quality: 70 })
  }
  const byId = id => page.getByTestId(id)
  try {
    const login = await context.request.post(`${WEB}/api/auth/login`, { data: devCredentials() })
    assert.equal(login.status(), 200, 'login')
    const csrf = (await context.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
    const name = `flow-shell-${template}-${Date.now().toString(36)}`
    const created = await context.request.post(
      `${WEB}/api/projects/from_template/${template}?name=${encodeURIComponent(name)}`,
      { headers: { 'X-CSRF-Token': csrf } })
    const body = await created.text()
    if (created.status() !== 200 && body.includes('network.nc is missing')) {
      // belgium needs a PyPSA-Eur workflow output; the others come from
      // project_templates/_build.py. Say so rather than pass silently.
      console.log(`    SKIP ${template}: not built in this checkout (run project_templates/_build.py)`)
      skipped.push(template)
      return failures
    }
    assert.equal(created.status(), 200, `create ${template}: ${body.slice(0, 200)}`)
    // Start in Guided once; a reload must keep the mode the journey chose.
    await page.addInitScript(() => {
      if (sessionStorage.getItem('flow-shell-journey')) return
      sessionStorage.setItem('flow-shell-journey', '1')
      localStorage.setItem('network-diagram:ui-mode', 'guided')
    })
    await page.goto(`${WEB}/projects`)
    await page.locator('a[href^="/app?project="]').first().click()
    await byId('hub-rail').waitFor({ timeout: 30_000 })
    const readFlow = async () => {
      const r = await context.request.get(`${WEB}/api/projects/${encodeURIComponent(name)}/flow`)
      assert.equal(r.status(), 200, 'GET /flow')
      return r.json()
    }
    let flow = await readFlow()
    for (let i = 0; i < 20 && flow.deferred; i++) { await page.waitForTimeout(500); flow = await readFlow() }
    const sec = id => flow.sections.find(s => s.section === id)
    check(SECTIONS.every(id => sec(id)), 'the server reports all ten sections')

    const open = async mode => {
      await byId(`ui-mode-${mode}`).click()
      await (mode === 'guided' ? byId('hub-rail') : byId('flow-section-connection')).waitFor({ timeout: 10_000 })
    }
    const railState = id => byId(RAIL_ID[id]).getAttribute('data-state')
    const railStates = async label => {
      for (const id of SECTIONS) {
        if (id === 'readiness' || id === 'results') continue
        const want = RAIL_STATE[sec(id).state]
        const got = await railState(id)
        if (got !== 'current') check(got === want, `${label} rail: ${id} shows ${sec(id).state} (${got})`)
      }
    }
    const sidebarStates = async label => {
      // The sidebar reads /flow on mount: wait for it before comparing.
      await page.waitForFunction(() => document.querySelector('[data-testid="flow-section-project"]')
        ?.getAttribute('data-state') !== 'unknown', null, { timeout: 20_000 }).catch(() => {})
      for (const id of SECTIONS) {
        check((await byId(`flow-section-${id}`).count()) === 1, `${label} sidebar: section ${id} is there once`)
        if (id === 'readiness' || id === 'results') continue
        const got = await byId(`flow-section-${id}`).getAttribute('data-state')
        check(got === sec(id).state, `${label} sidebar: ${id} shows ${sec(id).state} (${got})`)
      }
    }
    const issuesGroups = async () => {
      // Switching to Expert on the readiness section already opens Issues;
      // the row toggles, so click it only when the panel is closed.
      const findings = page.getByText(/^Findings/).first()
      if (!(await findings.isVisible().catch(() => false))) {
        await page.getByRole('button', { name: /Issues/ }).first().click()
      }
      await findings.waitFor({ timeout: 15_000 })
      await page.waitForTimeout(800)
      const groups = await page.locator('[data-testid^="issues-section-"]').evaluateAll(els =>
        els.map(e => ({ id: e.getAttribute('data-testid').replace('issues-section-', ''),
          rows: e.querySelectorAll('[data-testid="issue-title"]').length })))
      // The Findings card's own count: no finding may be dropped by the grouping.
      const head = (await findings.textContent()) ?? ''
      const total = Number((await page.getByText(/^Findings\s*\d+/).first().textContent())?.match(/\d+/)?.[0] ?? head.match(/\d+/)?.[0])
      const sum = groups.reduce((n, g) => n + g.rows, 0)
      if (groups.length) check(sum === total, `issues: the group rows (${sum}) add up to the Findings count (${total})`)
      return groups
    }
    const verdictOf = f => {
      const v = f.verdicts?.optimisation
      return { ready: v ? v.ready : f.run_ready, blocker: v ? v.blocker : f.run_blocker }
    }

    // 1. Guided rail: ten sections, the server's states, each section's open items
    await page.waitForFunction(id => !!document.querySelector(`[data-testid="${id}"]`), RAIL_ID.results)
    for (const id of SECTIONS) {
      await byId(RAIL_ID[id]).click()
      await page.locator(`[data-testid="section-view-${id}"], [data-testid="hub-card-${id}"]`).first()
        .waitFor({ timeout: 10_000 })
      const state = await railState(id)
      check(state === 'current', `guided: picking ${id} makes it the current step (${state})`)
      const items = byId('section-items')
      if (id !== 'readiness' && id !== 'results' && (await items.count())) {
        const shown = await items.locator('[data-testid="section-item"]').count()
        const n = sec(id).items.filter(isOpen).length
        check(shown === n, `guided: ${id} lists its ${n} open items (${shown})`)
      }
    }
    await byId(RAIL_ID.connection).click()
    await byId('section-view-connection').waitFor()
    await railStates('guided')
    await shot('1-guided-connection')

    // 2. Expert sidebar and the switch: one active section for both
    await open('expert')
    await sidebarStates('expert')
    await shot('2-expert-sidebar')
    await open('guided')
    check((await railState('connection')) === 'current', 'switch: Expert → Guided keeps connection')
    await open('expert')

    // 3. Run gate (baseline) and Issues
    const base = verdictOf(flow)
    check((await byId('run-blocker').count()) === (base.ready ? 0 : 1),
      `run gate: the header ${base.ready ? 'has no blocker' : 'names the blocker'} as the server says`)
    const groups0 = await issuesGroups()
    console.log(`    ..  Issues groups: ${groups0.map(g => `${g.id}(${g.rows})`).join(', ') || 'none'}`)
    check(groups0.every(g => g.id === 'other' || SECTIONS.includes(g.id)), 'issues: every group is a flow section')
    await shot('3-issues')
    await open('guided')
    check((await railState('readiness')) === 'current', 'switch: opening Issues in Expert moves Guided to readiness')
    await byId('readiness-ready').or(byId('readiness-blocked')).waitFor({ timeout: 10_000 })
    check((await byId(base.ready ? 'readiness-ready' : 'readiness-blocked').count()) === 1,
      `run gate (Guided): the Readiness card says ${base.ready ? 'ready' : 'blocked'}`)
    await shot('4-guided-readiness')

    // 4. A broken project: a bus without voltage and a generator whose
    //    minimum is above its maximum. The shell must follow /flow.
    const put = async (cls, patch) => {
      const rows = await (await context.request.get(`${WEB}/api/network/${cls}`)).json()
      const row = (Array.isArray(rows) ? rows : rows.items ?? Object.values(rows))[0]
      if (!row) return null
      const r = await context.request.put(`${WEB}/api/network/${cls}/${encodeURIComponent(row.name)}`,
        { headers: { 'X-CSRF-Token': csrf }, data: { ...row, ...patch } })
      assert.equal(r.status(), 200, `PUT ${cls}/${row.name}: ${(await r.text()).slice(0, 120)}`)
      return row.name
    }
    const bus = await put('buses', { v_nom: 0 })
    const gen = await put('generators', { p_min_pu: 1, p_max_pu: 0.2 })
    console.log(`    ..  broke bus ${bus} and generator ${gen}`)
    flow = await readFlow()
    const broken = verdictOf(flow)
    check(!broken.ready && !!broken.blocker, 'broken: the server refuses the run and names a blocker')
    // The edits are in memory; save them so the reload keeps them.
    const saved = await context.request.post(`${WEB}/api/projects/${encodeURIComponent(name)}`,
      { headers: { 'X-CSRF-Token': csrf } })
    assert.equal(saved.status(), 200, `save: ${(await saved.text()).slice(0, 120)}`)
    await page.reload()
    await byId('hub-rail').waitFor({ timeout: 30_000 })
    await page.waitForFunction(id => document.querySelector(`[data-testid="${id}"]`)?.getAttribute('aria-label')
      ?.includes('not known yet') === false, RAIL_ID.carriers, { timeout: 30_000 })
    await railStates('broken guided')
    await byId(RAIL_ID.readiness).click()
    await byId('readiness-blocked').waitFor({ timeout: 10_000 })
    await shot('5-broken-guided-readiness')
    await open('expert')
    await sidebarStates('broken expert')
    const blocker = byId('run-blocker')
    await blocker.waitFor({ timeout: 10_000 })
    const text = ((await blocker.textContent()) ?? '').trim()
    check(text === broken.blocker.message.trim(), `broken: the header gives the server's reason (${text.slice(0, 70)})`)
    check(await page.getByRole('button', { name: /Run/ }).filter({ hasText: /Run/ }).first().isDisabled(),
      'broken: Run is disabled')
    const groups = await issuesGroups()
    console.log(`    ..  Issues groups: ${groups.map(g => `${g.id}(${g.rows})`).join(', ') || 'none'}`)
    const wantGroups = new Set(flow.sections.filter(s => s.items.some(i => i.source === 'validation' && isOpen(i)))
      .map(s => s.section))
    for (const w of wantGroups) check(groups.some(g => g.id === w), `broken issues: a ${w} group (the server has validation items there)`)
    check(groups.every(g => g.id === 'other' || SECTIONS.includes(g.id)), 'broken issues: every group is a flow section')
    await shot('6-broken-issues')
    await blocker.click()
    await page.waitForTimeout(800)
    await open('guided')
    const target = broken.blocker.section
    check((await railState(target)) === 'current', `broken: the blocker opened its section, ${target}`)
    await shot('7-broken-blocker-section')

    // 5. Resume (design §7): a reload returns to the section the project was left in.
    await byId(RAIL_ID.commercial).click()
    await page.locator('[data-testid="section-view-commercial"], [data-testid="hub-card-commercial"]').first()
      .waitFor({ timeout: 10_000 })
    await page.reload()
    await byId('hub-rail').waitFor({ timeout: 30_000 })
    await page.waitForTimeout(1500)
    check((await railState('commercial')) === 'current', 'resume: a reload reopens the section it was left in')
    await open('expert')
    await page.reload()
    await byId('flow-section-connection').waitFor({ timeout: 30_000 })
    await open('guided')
    check((await railState('commercial')) === 'current', 'resume: Expert → reload → Guided still lands on that section')
    await shot('8-resume')

    check(errors.length === 0, `no page errors${errors.length ? `: ${errors[0]}` : ''}`)
  } catch (e) {
    failures.push(`${e.message.split('\n')[0]}`)
    await shot('failure').catch(() => {})
  } finally {
    await context.close()
  }
  return failures
}

const args = parseArgs(process.argv.slice(2))
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_BIN || undefined, headless: true, args: ['--no-sandbox'],
})
const failed = {}
const skipped = []
for (const template of args.templates) {
  console.log(`\n== ${template}`)
  const f = await walk(browser, template, args)
  if (f.length) failed[template] = f
}
await browser.close()
if (Object.keys(failed).length) {
  console.log('\nFAILED')
  for (const [t, f] of Object.entries(failed)) for (const m of f) console.log(`  ${t}: ${m}`)
  process.exit(1)
}
console.log(`\nflow shell journey: ${args.templates.length - skipped.length} template(s) ok`
  + (skipped.length ? `, skipped (not built): ${skipped.join(', ')}` : ''))
