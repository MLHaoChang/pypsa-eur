// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Template journey (E2E-TEMPLATES-1), section 2 Carriers & processes on the green ammonia template
 * (template-e2e 03): a carrier's Edit and a recipe's Edit open in the drawer without leaving Guided,
 * the Haber-Bosch hydrogen rate is changed there, and Expert Properties reads the same value.
 * Journey fit: template → set up §2 → the §3–5 and §7 pickers; a recipe edit puts results out of date.
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
const PROJECT = 'TJ green ammonia'

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
    `${base}/api/projects/from_template/eh_ammonia_staged?name=${encodeURIComponent(PROJECT)}`,
    { headers: { 'X-CSRF-Token': csrf }, timeout: 240000 })
  assert(made.ok(), `Template creation failed: ${made.status()} ${await made.text()}`)
  await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
  await page.addInitScript(project => {
    localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', 'guided')
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
  }, PROJECT)
  await page.goto(base + '/app')
  await page.getByTestId('hub-rail').waitFor()

  // §2 in Guided: the carriers, then the recipes by their plain names, no Link ids.
  await page.getByTestId('hub-rail-section-carriers').click()
  const list = page.getByTestId('hub-card-carriers')
  await list.getByText('Recipes', { exact: false }).first().waitFor()
  const text = await list.innerText()
  assert(!/haber_bosch|electrolyser_new|efficiency\d/.test(text), `Section 2 shows a raw id: ${text}`)
  assert.match(text, /Haber-Bosch/)
  assert.match(text, /5\.93 MWh H2 \+ 1,277\.55 kWh electricity per t NH3/)
  await page.screenshot({ path: path.join(artifacts, 'tj-03-guided-list.png') })

  // Adding a process unit is an Expert form (GAP03-ADD-1 a); Back returns to §2 and drops the form.
  await list.getByRole('button', { name: 'Add a process unit in Expert ›' }).click()
  const back = page.getByTestId('flow-return')
  assert.equal(await back.innerText(), 'Back to Carriers & processes')
  await page.getByRole('button', { name: 'Add to Network' }).waitFor()  // the form, not its "Loading…"
  await page.screenshot({ path: path.join(artifacts, 'tj-03-expert-add-back.png') })
  await back.click()
  await list.waitFor()
  assert.equal(await page.getByTestId('ui-mode-guided').getAttribute('aria-pressed'), 'true')

  // A carrier's Edit opens its card in the drawer, and Guided stays.
  await list.getByRole('button', { name: 'Edit H2', exact: true }).click()
  await page.getByTestId('field-drawer').getByText('Carrier Properties').waitFor()
  assert.equal(await page.getByTestId('ui-mode-guided').getAttribute('aria-pressed'), 'true')
  await page.getByTestId('field-drawer-close').click()

  // The recipe: Haber-Bosch's hydrogen per tonne of NH3, 5.93 → 6.1 MWh.
  await list.getByRole('button', { name: 'Edit Haber-Bosch recipe' }).click()
  const drawer = page.getByTestId('field-drawer')
  await drawer.getByTestId('recipe-card').waitFor()
  // The drawer's title names the recipe as the list does, never the Link id.
  assert.match(await drawer.innerText(), /Carriers & processes › Haber-Bosch recipe/)
  await drawer.getByTestId('recipe-edit').click()
  const h2 = drawer.getByLabel('H2 per tonne of NH3', { exact: true })
  assert.equal(await h2.inputValue(), '5.93')
  await h2.fill('0')
  await drawer.getByTestId('recipe-save').click()
  assert.equal(await drawer.getByTestId('recipe-refusal').innerText(), 'H2 per tonne of NH3: must be above 0')
  await page.screenshot({ path: path.join(artifacts, 'tj-03-guided-refusal.png') })
  await h2.fill('6.1')
  await drawer.getByTestId('recipe-save').click()
  await drawer.getByText('6.1 MWh', { exact: true }).waitFor()
  await page.screenshot({ path: path.join(artifacts, 'tj-03-guided-recipe.png') })

  // Expert reads the same value under the same label (EXPERT-GUIDED-1): the drawer's "Open in Expert"
  // shows the Link's Properties with the recipe card above its port efficiencies.
  await drawer.getByTestId('field-drawer-expert').click()
  const card = page.getByTestId('recipe-card')
  await card.waitFor()
  assert.match(await card.innerText(), /H2 per tonne of NH3\s*6\.1 MWh/)
  await page.screenshot({ path: path.join(artifacts, 'tj-03-expert-recipe.png') })

  assert.deepEqual(errors, [])
  report = { journey: 'template-e2e 03', passed: true, artifacts }
  await context.close()
} finally {
  await browser.close()
  await stop(child)
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
