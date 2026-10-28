/** LC-03 LC-T09/T10: a past run's view in the built app against the
 * authenticated native backend.
 * oj-grid is solved through the chat at Gas 50 EUR/MWh, re-priced to 60 and
 * solved again (real HiGHS, real execution cards). "Show me the capacity
 * results of the first run." opens the first run from its kept section: the
 * rendered source is historical, bound to that run, and shows gas LCOE 50, not
 * the current 60; the answer says the same number (LC-T09).
 * LC-T10: the user's own Clear, a newer turn about the latest run, and a
 * project switch each drop the past-run view; nothing of it is left showing.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-lc03-historical.mjs
 * No paid inference. Artifacts omit credentials/cookies.
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(process.env.PLAYWRIGHT_MODULE_PATH || import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repository = path.resolve(frontend, '../..')
const python = process.env.PYPSA_COMPOSER_PYTHON || path.join(repository, '.pixi/envs/test/bin/python')
const artifacts = process.env.LC03H_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-lc03h-'))
const PAST = 'Show me the capacity results of the first run.'
const LATEST = 'Show me the capacity results of the latest run.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'lc03h'],
    { cwd: repository, stdio: ['ignore', 'pipe', 'pipe'] })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timed out')), 120000)
    let output = ''
    child.stdout.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(value => value.startsWith('{"base":'))
      if (line) { clearTimeout(timer); resolve({ child, ...JSON.parse(line) }) }
    })
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}`)) })
  })
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 12000)
  await exited
  clearTimeout(timer)
}

async function send(page, text) {
  // Full-screen Results hides the dock transcript; the companion has its own input.
  if (await page.getByTestId('chat-input').isVisible()) {
    await page.getByTestId('chat-input').fill(text)
    await page.getByTestId('chat-send').click()
    return
  }
  const input = page.getByTestId('companion-live-input')
  const live = page.getByTestId('companion-live')
  for (let i = 0; await live.getAttribute('aria-pressed') !== 'true'; i += 1) {
    assert(i < 3, 'the in-page chat did not open')
    await live.click()
    await page.waitForTimeout(500)
  }
  await input.fill(text).catch(async error => {
    await page.screenshot({ path: path.join(artifacts, 'lc03h-failed-send.png') })
    throw error
  })
  await page.getByTestId('companion-live-send').click()
}

/** The answer of the nth turn carrying `tag`, from the floating companion. */
async function answer(page, tag, nth = 0) {
  const words = page.getByTestId('chat-messages').getByText(new RegExp(tag))
  await words.nth(nth).waitFor({ state: 'attached', timeout: 60000 }).catch(async error => {
    await page.screenshot({ path: path.join(artifacts, `lc03h-failed-${tag}.png`) })
    throw error
  })
  return (await words.nth(nth).textContent()).trim()
}

/** The past-run view as rendered: its source binding and the kept gas row. */
async function pastView(page, runId) {
  const shown = page.locator(`[data-result-binding][data-result-tab="capex"]`)
  await shown.first().waitFor({ timeout: 30000 })
  const binding = JSON.parse(await shown.first().getAttribute('data-result-binding'))
  const kept = JSON.parse(await shown.first().locator('pre').first().textContent())
  assert.equal(await page.getByTestId('discussed-run-chrome').getAttribute('data-run-id'), runId)
  return { binding, gas: kept.economics_by_carrier.by_carrier.gas }
}

async function noPastView(page) {
  await page.getByTestId('discussed-run-chrome').waitFor({ state: 'detached', timeout: 30000 })
  assert.equal(await page.locator('[data-result-binding*="\\"scope\\":\\"historical\\""]').count(), 0)
  assert.equal(await page.getByText(/^Retained run /).count(), 0)
}

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file, network_file: network } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
let report
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto(base)
  await page.locator('#email').fill(credentials.email)
  await page.locator('#password').fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects\?signed_in=/)
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  assert(csrf, 'Real login must issue CSRF')
  const headers = { 'X-CSRF-Token': csrf }
  const importGrid = async () => {
    const imported = await context.request.post(base + '/api/io/import/netcdf', { headers,
      multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
    assert(imported.ok(), `NetCDF import failed: ${imported.status()}`)
  }
  // A second project first, so the switch in LC-T10 has somewhere to go.
  await importGrid()
  assert((await context.request.post(base + '/api/projects/oj-grid-other', { headers })).ok())
  await importGrid()
  assert((await context.request.post(base + '/api/projects/oj-grid', { headers })).ok())
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(() => {
    localStorage.setItem('network-diagram:current-project', 'oj-grid')
    localStorage.setItem('network-diagram:assistant-dock', 'open')
  })
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()

  // Two real solves through the ordinary execution card, Gas re-priced between them.
  const runs = []
  for (const n of [1, 2]) {
    if (n === 2) {
      const priced = await context.request.put(base + '/api/network/generators/Gas', { headers,
        data: { name: 'Gas', bus: 'B1', marginal_cost: 60 } })
      assert(priced.ok(), `Re-pricing Gas failed: ${priced.status()}`)
    }
    await send(page, 'Run the simulation.')
    await page.getByTestId('chat-confirm-approve').last().click()
    const started = page.getByTestId('chat-messages').getByText(/The solve was started \(job fg:[0-9a-f]+\)/).nth(n - 1)
    await started.waitFor({ state: 'attached', timeout: 60000 })
    runs.push('fg-' + (await started.textContent()).match(/fg:([0-9a-f]+)/)[1])
    for (let i = 0; ; i += 1) {
      const status = await (await context.request.get(base + '/api/simulation/status')).json()
      if (status.status === 'completed') break
      assert(i < 240, `solve ${n} did not complete: ${JSON.stringify(status)}`)
      await page.waitForTimeout(500)
    }
    await page.waitForTimeout(2000) // the detached monitor publishes the run manifest
  }
  assert.notEqual(runs[0], runs[1])

  const openPast = async nth => {
    await send(page, PAST)
    // Binding the conversation to a run is a write: the user approves its card.
    const approvals = await page.getByTestId('chat-confirm-approve').count()
    await page.getByTestId('chat-confirm-approve').nth(approvals).click({ timeout: 60000 })
    const words = await answer(page, 'LC03H_PAST', nth)
    const view = await pastView(page, runs[0])
    return { words, ...view }
  }

  // LC-T09: the first run, from its kept section, never the current results.
  const past = await openPast(0)
  await page.screenshot({ path: path.join(artifacts, 'lc03h-past-run.png') })
  assert.match(past.words, new RegExp(`^LC03H_PAST Run ${runs[0]} \\(historical\\): gas LCOE 50(\\.0+)? EUR/MWh\\.$`))
  assert.equal(past.binding.scope, 'historical')
  assert.equal(past.binding.run_id, runs[0])
  assert.equal(past.binding.section, 'results_capacity')
  assert.equal(past.binding.project_name, 'oj-grid')
  assert.equal(past.gas.lcoe_eur_per_mwh.total, 50)   // the hand oracle; the current run's is 60

  // LC-T10 (1): the user's own Clear supersedes the past-run view.
  await page.getByTestId('discussed-run-chrome').getByRole('button', { name: 'Clear' }).click()
  await noPastView(page)

  // LC-T10 (2): a newer turn about the latest run supersedes it too.
  await openPast(1)
  await send(page, LATEST)
  const latest = await answer(page, 'LC03H_LATEST')
  assert(Math.abs(Number(latest.match(/gas LCOE (\S+) EUR\/MWh/)[1]) - 60) < 1e-6, latest)
  await noPastView(page)
  await page.screenshot({ path: path.join(artifacts, 'lc03h-latest.png') })

  // LC-T10 (3): a project switch leaves nothing of the past run behind.
  await openPast(2)
  // The user's own switch: the Projects page, then the other project.
  await page.getByRole('button', { name: /^Projects$/ }).first().click()
  await page.waitForURL(/\/projects/)
  // The whole card is its link: a pointer click on the card's name opens it.
  const card = await page.getByText('oj-grid-other', { exact: true }).first().boundingBox()
  await page.mouse.click(card.x + card.width / 2, card.y + card.height / 2)
  await page.waitForURL(/\/app/)
  await page.getByTestId('chat-input').waitFor({ state: 'attached' }).catch(async error => {
    await page.screenshot({ path: path.join(artifacts, 'lc03h-failed-switch.png') })
    throw error
  })
  await page.waitForFunction(() => localStorage.getItem('network-diagram:current-project') === 'oj-grid-other',
    null, { timeout: 30000 })
  await page.getByTestId('chat-input').waitFor({ state: 'attached' })
  await noPastView(page)
  await page.screenshot({ path: path.join(artifacts, 'lc03h-switched.png') })

  assert.deepEqual(errors, [])
  report = { journey: 'LC-T09/T10 historical', passed: true, solves: runs.length,
    past: { answer: past.words.replace(runs[0], '<first run>'), scope: past.binding.scope,
      section: past.binding.section, gasLcoe: past.gas.lcoe_eur_per_mwh.total },
    superseded: ['clear', 'newer turn', 'project switch'], latest,
    screenshots: ['lc03h-past-run.png', 'lc03h-latest.png', 'lc03h-switched.png'] }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
