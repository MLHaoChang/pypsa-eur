/** LC-16 slice C (UX condition 7) in the built app against the authenticated native backend:
 * settings edits from chat get the before/after card and the read-back receipt that asset edits
 * have. The loopback wire plays update_solver_config (full → rolling, with the note; discount rate
 * and lifetime), update_meta (network name), set_investment_period_weightings (two periods), and a
 * numeric bulk_update_components that is refused with the preview form and lands on the
 * multi-asset preview card. Each card before approval and its receipt after, Expert and Guided.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-lc16c-settings.mjs
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
const artifacts = process.env.LC16C_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'lc16c-settings-'))

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'lc16c'],
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
  if (!await input.isVisible()) await page.getByTestId('companion-live').click()
  await input.fill(text)
  await page.getByTestId('companion-live-send').click()
}

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file, network_file: network } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
let result
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
  const imported = await context.request.post(base + '/api/io/import/netcdf', { headers,
    multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
  assert(imported.ok(), `NetCDF import failed: ${imported.status()}`)
  const saved = await context.request.post(base + '/api/projects/oj-grid', { headers })
  assert(saved.ok(), `Native project creation failed: ${saved.status()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(() => {
    localStorage.setItem('network-diagram:current-project', 'oj-grid')
    localStorage.setItem('network-diagram:assistant-dock', 'open')
  })
  await page.goto(base + '/app')
  await page.getByTestId('chat-input').waitFor()
  await page.getByTestId('ui-mode-expert').click()
  const json = async url => (await context.request.get(base + url)).json()
  const state = async () => {
    const solver = await json('/api/simulation/solver_config')
    const lines = Object.fromEntries((await json('/api/network/lines')).map(l => [l.name, l.s_nom]))
    return { strategy: solver.solve_strategy, horizon: solver.rolling_horizon, rate: solver.discount_rate,
      lifetime: solver.default_lifetime, name: (await json('/api/network/meta')).name,
      weightings: (await json('/api/network/investment_periods')).weightings, L1: lines.L1, L2: lines.L2 }
  }
  const BEFORE = await state()
  assert.equal(BEFORE.strategy, 'full')
  assert.deepEqual((await json('/api/network/investment_periods')).periods, [2030, 2040])
  async function reset() {
    const ok = async r => assert((await r).ok(), 'reset failed')
    await ok(context.request.put(base + '/api/simulation/solver_config', { headers, data: {
      solve_strategy: 'full', rolling_horizon: BEFORE.horizon, discount_rate: BEFORE.rate, default_lifetime: BEFORE.lifetime } }))
    await ok(context.request.put(base + '/api/network/meta', { headers, data: { name: BEFORE.name } }))
    await ok(context.request.patch(base + '/api/network/investment_period_weightings', { headers,
      data: { updates: { 2030: { years: 1, objective: 1 }, 2040: { years: 1, objective: 1 } } } }))
    await ok(context.request.patch(base + '/api/network/_bulk', { headers,
      data: { component_class: 'Line', names: ['L1', 'L2'], updates: { s_nom: BEFORE.L1 } } }))
    assert.deepEqual(await state(), BEFORE)
  }

  const CASES = [
    ['solver', 'Switch the solver to rolling with a 48-snapshot horizon.', s => s.strategy === 'rolling' && s.horizon === 48],
    ['economics', 'Set the discount rate to 5 % and the default lifetime to 30 years.', s => s.rate === 0.05 && s.lifetime === 30],
    ['name', 'Rename the network to Harbour grid.', s => s.name === 'Harbour grid'],
    ['weightings', 'Weight 2030 and 2040 at 10 years each, 2040 objective 0.5.', s => JSON.stringify(s.weightings).includes('0.5')],
    ['bulk', 'Set L1 and L2 to 500 MVA.', s => s.L1 === 500 && s.L2 === 500],
  ]
  const shots = []
  const report = {}
  for (const mode of ['expert', 'guided']) {
    await page.getByTestId(`ui-mode-${mode}`).click()
    await reset()
    for (const [id, sentence, applied] of CASES) {
      const cards = await page.getByTestId('chat-edit-card').count()
      await send(page, sentence)
      const card = page.getByTestId('chat-edit-card').nth(cards)
      await card.waitFor({ timeout: 60000 }).catch(async error => {
        throw new Error(`${error.message}\n${(await page.getByTestId('chat-messages').innerText()).slice(-1500)}`)
      })
      await page.getByTestId('chat-confirm-approve').last().waitFor()
      const before = await state()
      assert(!applied(before), `${id}: nothing changes before approval`)
      const words = await card.innerText()
      if (id === 'solver') assert.equal(await card.getByTestId('chat-edit-card-note').count()
        + await card.getByTestId('chat-edit-row-note').count() > 0, true, 'the rolling note is on the card')
      if (id === 'bulk') assert.equal(await card.getByTestId('chat-edit-row-typed').count(), 2, 'one card for both lines')
      await card.scrollIntoViewIfNeeded()
      for (const [suffix, target] of [['before', page], ['before-close', card]]) {
        shots.push(`lc16c-${id}-${mode}-${suffix}.png`)
        await target.screenshot({ path: path.join(artifacts, shots.at(-1)) })
      }
      const receipts = await page.getByTestId('chat-edit-receipt').count()
      await page.getByTestId('chat-confirm-approve').last().click()
      const receipt = page.getByTestId('chat-edit-receipt').nth(receipts)
      await receipt.waitFor({ timeout: 60000 })
      assert(applied(await state()), `${id}: the approval wrote the change`)
      await page.waitForFunction(() => document.querySelector('[data-testid="chat-input"]')?.placeholder !== 'streaming…', null, { timeout: 60000 })
      await receipt.scrollIntoViewIfNeeded()
      for (const [suffix, target] of [['after', page], ['after-close', receipt]]) {
        shots.push(`lc16c-${id}-${mode}-${suffix}.png`)
        await target.screenshot({ path: path.join(artifacts, shots.at(-1)) })
      }
      report[`${id}-${mode}`] = { card: words, receipt: await receipt.innerText() }
    }
  }

  assert.deepEqual(errors, [])
  result = { journey: 'LC-16 slice C', passed: true, report, screenshots: shots, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(result, null, 2))
