/** LC-03 OJ-01 in the built app against the authenticated native backend.
 * "Show me the results of the latest calculation, the load flow results."
 * The oj-grid fixture is imported, solved once through the chat (real HiGHS,
 * real execution card), then asked OJ-01. The loopback wire plays the model's
 * tool calls; every argument and number in its answer comes from this turn's
 * native results. Asserts the Load flow tab renders the bound source, the
 * browser ACK lets the answer say "Showing", and the hand-calculated oracle
 * (tests/oj_grid.py: T1 45 %, L1 and L2 15 %).
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-copilot-oj01.mjs
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
const artifacts = process.env.OJ01_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'copilot-oj01-'))
const SENTENCE = 'Show me the results of the latest calculation, the load flow results.'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--journey', 'oj01'],
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

  // Two real solves through the ordinary execution card; "latest" must be the second.
  const runs = []
  for (const n of [1, 2]) {
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

  const started = Date.now()
  await send(page, SENTENCE)
  const answer = page.getByTestId('chat-messages').getByText(/OJ01_ANSWER/)
  const companion = page.getByTestId('companion-answer-text').getByText(/OJ01_ANSWER/)
  await companion.waitFor({ timeout: 60000 })
  await answer.waitFor({ state: 'attached' })
  const words = (await answer.textContent()).trim()
  const tab = page.locator('[data-assurance-tab="loadflow"]')
  const shown = page.locator('[data-result-binding][data-result-tab="loadflow"]')
  await shown.first().waitFor()
  const binding = JSON.parse(await shown.first().getAttribute('data-result-binding'))
  const oj01Seconds = (Date.now() - started) / 1000
  await page.screenshot({ path: path.join(artifacts, 'oj01-loadflow.png') })

  assert.match(words, /^OJ01_ANSWER Showing the Load flow tab for run \S+ \(finished [^)]+\), current: true\./i)
  assert.equal(words.match(/for run (\S+) /)[1], runs[1], 'latest is the newest completed run')
  assert.match(words, /Highest loading: T1 45(\.0)? % \(45(\.0)? MW of 100(\.0)? MVA\), L1 15(\.0)? % \(45(\.0)? MW of 300(\.0)? MVA\), L2 15(\.0)? % \(45(\.0)? MW of 300(\.0)? MVA\)/)
  // The rendered table shows the same oracle values with their units.
  const row = name => page.locator('tr', { hasText: name }).first().innerText()
  for (const [name, rating] of [['T1', '100.00'], ['L1', '300.00'], ['L2', '300.00']]) {
    const text = await row(name)
    assert.match(text, new RegExp(`${rating}[\\s\\S]*45\\.00`), `${name} row: ${text}`)
  }
  assert.match(await page.getByText(/^Peak flow$/).first().locator('..').innerText(), /45\.0\s*MW/)
  assert.match(words, /Active-power proxy/)
  assert.equal(await tab.count(), 1)
  // Transcript and floating companion carry the same words (LC-C7).
  assert.equal((await companion.textContent()).trim(), words)
  assert.equal(binding.project_name, 'oj-grid')
  assert.equal(binding.scope, 'current')
  assert.equal(binding.source, 'lopf')
  assert.deepEqual([binding.window_from, binding.window_to], [0, 23])
  // OJ-03: "Show me something else" never repeats Load flow and opens a section with data.
  await send(page, 'Show me something else.')
  const other = page.getByTestId('companion-answer-text').getByText(/OJ03_ANSWER/)
  await other.waitFor({ timeout: 60000 })
  const otherWords = (await other.textContent()).trim()
  const otherTab = otherWords.match(/OJ03_ANSWER (?:Showing|Opened) (\w+);/)[1]
  assert.notEqual(otherTab, 'loadflow')
  assert.match(otherWords, /already shown: loadflow\./)
  assert.doesNotMatch(otherWords.split('already shown')[0], /loadflow/)
  await page.locator(`[data-assurance-tab="${otherTab}"]`).waitFor({ state: 'attached' })
  await page.screenshot({ path: path.join(artifacts, 'oj03-something-else.png') })

  assert.deepEqual(errors, [])
  report = { journey: 'OJ-01', passed: true, sentence: SENTENCE, solves: runs.length, latestIsSecond: true, answer: words.replace(/run \S+/, 'run <run_id>'),
    shown: { tab: 'loadflow', scope: binding.scope, source: binding.source,
      window: [binding.window_from, binding.window_to] }, seconds: oj01Seconds,
    screenshot: 'oj01-loadflow.png',
    oj03: { answer: otherWords, opened: otherTab, screenshot: 'oj03-something-else.png' } }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
