// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Template journey (E2E-TEMPLATES-1), the data centre's investment case (investment walkthrough
 * issue 02): from the template's defaults, Guided and Expert name the same needs before the solve,
 * "Run the solve" clears them, the case runs, and both screens give the same NPV. The case's flags
 * are all worded (projectFlagKeys.json) and its prose carries no raw code.
 * Journey fit: template → readiness (solve) → results → decide (investment case).
 *
 * Runs against the built app and the authenticated native backend (scripts/composer-fixture.py),
 * like the owner journeys. Env: TJ_ARTIFACTS (screenshots), PYPSA_COMPOSER_PYTHON, PLAYWRIGHT_MODULE_PATH.
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import {
  api, assert, ensurePanel, frontend, goSection, openBrowser, startBackend, stopBackend, tmpdir,
} from './template-journeys-lib.mjs'

const artifacts = process.env.TJ_ARTIFACTS || await tmpdir('template-journey-')
const PROJECT = 'TJ data centre investment'
// A raw code: two or more lower-case words joined by underscores (G5).
const SNAKE = /\b[a-z]+(?:_[a-z0-9]+)+\b/g
const keys = JSON.parse(await fs.readFile(path.join(frontend, 'src/pages/results/investment/projectFlagKeys.json'), 'utf8'))

/** Guided's "−€18.2 M" (the figure and its unit may sit on two lines) or Expert's "-18,220,995.22"
 *  → millions rounded to 0.1, the rule Guided displays, sign kept; null when no figure is shown. */
const UNIT = { k: 1e-3, M: 1, bn: 1e3 }
function millions(text, { euro }) {
  const flat = (text ?? '').replace(/\s+/g, ' ')
  const m = euro ? flat.match(/([−-])?\s*€\s*([\d,]+(?:\.\d+)?)\s*(k|M|bn)?\b/) : flat.match(/([−-])?\s*([\d,]+(?:\.\d+)?)/)
  if (!m) return null
  const value = Number(m[2].replace(/,/g, '')) * (m[1] ? -1 : 1)
  const mn = euro ? value * UNIT[m[3] ?? 'M'] : value / 1e6
  return Math.sign(mn) * Math.round(Math.abs(mn) * 10) / 10   // half away from zero, either sign
}

const needsOf = async scope => (await scope.getByTestId('ic-needs').innerText()).replace(/\s+/g, ' ').trim()
const shot = (page, name) => page.screenshot({ path: path.join(artifacts, `investment-${name}.png`) }).catch(() => {})

/** Expert › Results › Commercial & finance › Investment case, from the Guided tile's link. */
async function openExpertCase(ctx) {
  const { page } = ctx
  await goSection(ctx, 'Results & reports')
  // The headline's link once the study has run; the Investment tile's own link before.
  const hub = page.getByTestId('hub-design-panel')
  await hub.getByRole('button', { name: /^Investment case ›/ }).or(hub.getByTestId('hub-chapter-investment-open')).first().click()
  await page.getByText('Investment case', { exact: true }).last().click()
  return page.getByTestId('ic-run-panel').last()
}

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file } = await startBackend(storage)
const browser = await openBrowser()
let failed = false
let page = null
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  page = await context.newPage()
  page.setDefaultTimeout(30000)
  const ctx = { page, context, base }
  await page.goto(base)
  await page.locator('#email').fill(credentials.email)
  await page.locator('#password').fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  assert(csrf, 'Real login must issue CSRF')
  // Setup only: every value stays at the template's default, as the form's Create does.
  const made = await context.request.post(
    `${base}/api/projects/from_template/eh_datacenter?name=${encodeURIComponent(PROJECT)}`,
    { headers: { 'X-CSRF-Token': csrf }, data: { dc_load: {} }, timeout: 240000 })
  assert(made.ok(), `Template creation failed: ${made.status()} ${await made.text()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(project => {
    localStorage.setItem('network-diagram:current-project', project)
  }, PROJECT)
  await page.goto(`${base}/app`)
  await page.getByRole('button', { name: 'Skip tour' }).click({ timeout: 5000 }).catch(() => {})

  // 1. Before the solve: the same needs, in the same words, in Guided and Expert (G6).
  const panel = await ensurePanel(ctx)
  await goSection(ctx, 'Results & reports')
  const guidedRun = panel.getByTestId('ic-run-panel')
  await guidedRun.getByTestId('ic-run').click()
  await guidedRun.getByTestId('ic-needs').waitFor()
  const guidedNeeds = await needsOf(guidedRun)
  assert.match(guidedNeeds, /solved project/i, `Guided needs: ${guidedNeeds}`)
  const expertRun = await openExpertCase(ctx)
  await expertRun.getByTestId('ic-run').click()
  await expertRun.getByTestId('ic-needs').waitFor()
  const expertNeeds = await needsOf(expertRun)
  assert.equal(expertNeeds, guidedNeeds, 'Guided and Expert name the investment case needs in the same words')
  await shot(page, '1-needs-expert')

  // 2. "Run the solve" from the Guided needs list, then the case from Guided.
  const guided = await ensurePanel(ctx)
  await goSection(ctx, 'Results & reports')
  const again = guided.getByTestId('ic-run-panel')
  await again.getByTestId('ic-run').click()
  await again.getByTestId('ic-needs').getByRole('button', { name: /Run the solve/ }).click()
  const deadline = Date.now() + 300000
  for (;;) {
    // A connection reset while the solve holds the worker is retried until the deadline.
    const r = await api(ctx, 'POST', '/api/results/investment_case').catch(() => null)
    if (r && r.status() !== 409) { assert(r.ok(), `Investment case start: ${r.status()} ${await r.text()}`); break }
    assert(Date.now() < deadline, 'The solve did not clear the investment case need within 300 s')
    await page.waitForTimeout(5000)
  }
  for (;;) {
    const r = await api(ctx, 'GET', '/api/results/investment_case').catch(() => null)
    if (r && r.status() === 200 && (await r.json()).status !== 'running') break
    assert(Date.now() < deadline, 'The investment case did not finish within 300 s')
    await page.waitForTimeout(3000)
  }

  // 3. The same NPV, sign included, rounded to 0.1 M as Guided shows it (G6).
  await page.reload()
  const after = await ensurePanel(ctx)
  await goSection(ctx, 'Results & reports')
  // The headline tile once the study has run; the Investment tile in "The whole picture" before.
  const figureTile = after.getByTestId('hub-results-npv').or(after.getByTestId('hub-chapter-investment')).first()
  let guidedFigure = ''
  for (const until = Date.now() + 30000; Date.now() < until && millions(guidedFigure, { euro: true }) === null;) {
    guidedFigure = await figureTile.innerText()
    await page.waitForTimeout(1000)
  }
  const guidedNpv = millions(guidedFigure, { euro: true })
  await shot(page, '2-guided-npv')
  await openExpertCase(ctx)
  const expertCell = page.getByTestId('ic-headline-npv_at_wacc').last()
  await expertCell.waitFor()
  const expertNpv = millions(await expertCell.innerText(), { euro: false })
  assert(guidedNpv !== null, `Guided shows no NPV: ${guidedFigure}`)
  assert(expertNpv !== null, 'Expert shows no NPV')
  assert.equal(guidedNpv, expertNpv, `Guided NPV ${guidedNpv} M (${JSON.stringify(guidedFigure)}) and Expert NPV ${expertNpv} M differ`)
  await shot(page, '3-expert-case')

  // 4. Every flag the live case emits is worded or stated elsewhere (G2), and the prose has no code (G5).
  const report = await (await api(ctx, 'GET', '/api/results/investment_case/report?detail=full')).json()
  const flags = report.sections?.project?.payload?.flags ?? []
  assert(flags.length > 0, 'The data-centre case reports its flags')
  // A stated flag is exempt only when the report states it (financeModel.statedFlags): the site-today
  // basis under the site-today sentence, the island-relaxed baseline under the counterfactual notes.
  const cf = report.sections?.project?.payload?.counterfactual ?? {}
  const statedNow = { 'counterfactual_basis:site_today': cf.counterfactual_basis === 'site_today',
    baseline_island_rows_relaxed: Array.isArray(cf.notes) && cf.notes.length > 0 }
  const stated = f => keys.stated.includes(f) && statedNow[f] === true
  const unworded = flags.filter(f => !stated(f) && !keys.worded.includes(f.split(':')[0]))
  assert.deepEqual(unworded, [], 'Every data-centre flag has words in projectFlagWords')
  for (const id of ['ic-run-panel', 'ic-headlines', 'ic-counterfactual']) {
    const text = await page.getByTestId(id).last().innerText()
    const raw = [...text.matchAll(SNAKE)].map(m => m[0])
    assert.deepEqual(raw, [], `${id} shows raw codes`)
    assert(!/no description yet/.test(text), `${id} shows a flag or reason with no words (its code is in the title)`)
  }
  console.log(JSON.stringify({ ok: true, guidedNeeds, guidedNpv, expertNpv, flags: flags.length }))
} catch (error) {
  failed = true
  console.error(error)
  if (page) {
    await shot(page, 'FAIL')
    console.error((await page.locator('body').innerText().catch(() => '')).slice(0, 3000))
  }
} finally {
  await browser.close()
  await stopBackend(child)
}
process.exit(failed ? 1 : 0)
