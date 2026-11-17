// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Template journey (E2E-TEMPLATES-1), section 3 Connection & grid on the data centre template
 * (template-e2e 02): the connection limit is set in Guided and read back in Expert, and the
 * "Import efficiency (assumed)" Edit opens the import link's own values, not a blank form.
 * Journey fit: template → set up §3 → readiness. The limit feeds the energization check (§4, §9).
 *
 * Runs against the built app and the authenticated native backend (scripts/composer-fixture.py),
 * like the owner journeys. Env: TJ_ARTIFACTS (screenshots), PYPSA_COMPOSER_PYTHON, PLAYWRIGHT_MODULE_PATH.
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
const artifacts = process.env.TJ_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'template-journey-'))
const PROJECT = 'TJ data centre'

function fixture(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--no-model'],
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

/** Every input value in the field drawer, in order. */
const drawerValues = page => page.getByTestId('field-drawer').locator('input')
  .evaluateAll(els => els.map(e => e.value))

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
let report
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.setDefaultTimeout(30000)
  const errors = []
  page.on('pageerror', error => errors.push(String(error)))
  await page.goto(base)
  await page.locator('#email').fill(credentials.email)
  await page.locator('#password').fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  assert(csrf, 'Real login must issue CSRF')
  // Setup only: the template's own decisions are made through the UI below.
  const made = await context.request.post(
    `${base}/api/projects/from_template/eh_datacenter?name=${encodeURIComponent(PROJECT)}`,
    { headers: { 'X-CSRF-Token': csrf }, data: { dc_load: {} }, timeout: 240000 })
  assert(made.ok(), `Template creation failed: ${made.status()} ${await made.text()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(project => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', 'guided')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  }, PROJECT)
  await page.goto(base + '/app')
  await page.getByTestId('hub-rail').waitFor()

  // §3 in Guided: the limit is a row with its own Edit, and the header names the connection in plain words.
  await page.getByTestId('hub-rail-step-site').click()
  const row = page.getByTestId('hub-site-connection-limit')
  await row.waitFor()
  assert.match(await row.innerText(), /Connection limit: 40 MW/)
  const rail = await page.getByRole('navigation', { name: 'Project flow sections' }).innerText()
  assert(!rail.includes('grid_import'), `The rail shows a raw link id: ${rail}`)
  await page.getByTestId('hub-site-connection-limit-edit').click()
  const input = page.getByTestId('field-drawer').getByLabel('Connection limit (MW)', { exact: true })
  assert.equal(await input.inputValue(), '40')
  await input.fill('45')
  await page.getByTestId('field-drawer').getByRole('button', { name: 'Save connection limit' }).click()
  await page.getByTestId('field-drawer').getByTestId('connection-limit-connection_limit')
    .getByText('45 MW', { exact: true }).waitFor()
  await page.screenshot({ path: path.join(artifacts, 'tj-02-guided-limit.png') })
  await page.getByTestId('field-drawer-close').click()
  await row.getByText('45 MW').waitFor()

  // Expert reads back the same value under the same label (EXPERT-GUIDED-1): the switch keeps the
  // section, so Connection & grid's panel opens with its Connection limits.
  await page.getByRole('button', { name: 'Expert', exact: true }).click()
  const line = page.getByTestId('connection-limit-connection_limit')
  await line.waitFor()
  assert.match(await line.innerText(), /Connection limit: 45 MW/)
  assert.equal(await line.getByLabel('Connection limit (MW)', { exact: true }).inputValue(), '45')
  await page.screenshot({ path: path.join(artifacts, 'tj-02-expert-limit.png') })

  // Back in Guided, "Import efficiency (assumed)" opens the import link's own values: its name,
  // the limit set above as its capacity, and 99.5 % efficiency.
  await page.getByRole('button', { name: 'Guided', exact: true }).click()
  await page.getByTestId('hub-rail-step-site').click()
  await page.getByTestId('assumption-row').filter({ hasText: 'Import efficiency' })
    .getByTestId('assumption-edit').click()
  await page.getByTestId('field-drawer').waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="field-drawer"] input')]
    .some(e => e.value === 'grid_import'))
  const values = await drawerValues(page)
  assert(values.includes('45'), `Capacity is not the limit set in Guided: ${values.slice(0, 12)}`)
  assert(values.includes('99.5'), `Efficiency is blank: ${values.slice(0, 12)}`)
  await page.screenshot({ path: path.join(artifacts, 'tj-02-import-efficiency.png') })

  assert.deepEqual(errors, [])
  report = { journey: 'template-e2e 02', passed: true, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
