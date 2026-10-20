/** L2 (LC-04): where the floating companion launcher sits with the dock closed,
 * over the canvas, the Properties panel and the Guided Hub design page. Writes
 * screenshots and the launcher's box against what it covers.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/shot-launcher-overlap.mjs
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
const artifacts = process.env.LAUNCHER_ARTIFACTS || await fs.mkdtemp(path.join(os.tmpdir(), 'launcher-'))

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

const box = async locator => (await locator.count()) ? locator.first().boundingBox() : null
const overlap = (a, b) => !!a && !!b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

const storage = path.join(artifacts, 'storage')
const { child, base, credentials_file: file, network_file: network } = await fixture(storage)
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
const report = {}
try {
  const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
  for (const [label, width, height] of [['wide', 1440, 900], ['laptop', 1280, 720]]) {
    const context = await browser.newContext({ viewport: { width, height } })
    const page = await context.newPage()
    await page.goto(base)
    await page.locator('#email').fill(credentials.email)
    await page.locator('#password').fill(credentials.password)
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    await page.waitForURL(/\/projects\?signed_in=/)
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
    const headers = { 'X-CSRF-Token': csrf }
    await context.request.post(base + '/api/io/import/netcdf', { headers,
      multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(network) } } })
    await context.request.post(base + '/api/projects/oj-grid', { headers })
    await page.addInitScript(() => {
      localStorage.setItem('network-diagram:current-project', 'oj-grid')
    })
    await page.goto(base + '/app')
    await page.getByTestId('ui-mode-expert').click()
    // One solve through the ordinary chat card, so Results has data.
    if (!await page.getByTestId('chat-input').isVisible()) await page.getByText('Assistant', { exact: true }).first().click()
    await page.getByTestId('chat-input').fill('Run the simulation.')
    await page.getByTestId('chat-send').click()
    await page.getByTestId('chat-confirm-approve').last().click()
    for (let i = 0; ; i += 1) {
      const status = await (await context.request.get(base + '/api/simulation/status')).json()
      if (status.status === 'completed') break
      assert(i < 240, 'solve did not complete')
      await page.waitForTimeout(500)
    }
    await page.evaluate(() => localStorage.setItem('network-diagram:assistant-dock', 'closed'))
    await page.reload()
    const launcher = page.getByTestId('companion-launcher')
    await launcher.waitFor()
    await page.keyboard.press('Control+k'); await page.keyboard.type('L2')
    await page.getByText(/^Line · /).first().waitFor(); await page.keyboard.press('Enter')
    await page.waitForTimeout(800)
    const idle = async () => {
      await page.mouse.move(5, height - 5)
      await page.evaluate(() => (document.activeElement instanceof HTMLElement) && document.activeElement.blur())
      await page.waitForTimeout(600)
    }
    const covers = async targets => {
      const l = await box(launcher)
      const out = { launcher: l }
      for (const [k, v] of Object.entries(targets)) out[k] = overlap(l, await box(v))
      return out
    }
    const canvasTargets = {
      properties_panel: page.getByRole('button', { name: 'Edit Parameters' }).locator('xpath=ancestor::*[contains(@class,"border-l")][1]'),
      edit_button: page.getByRole('button', { name: 'Edit Parameters' }),
      minimap: page.locator('.react-flow__minimap'),
      snapshot_bar: page.locator('.z-\\[900\\]'),
      canvas_controls: page.locator('.react-flow__controls'),
      bottom_panel: page.getByRole('button', { name: /^Log$/ }).locator('xpath=ancestor::*[contains(@class,"border-t")][1]'),
    }
    await idle()
    report[`${label}_expert_idle`] = await covers(canvasTargets)
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-expert-idle.png`) })
    { const c = await box(page.getByTestId('companion-character')); await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2) }
    await page.waitForTimeout(500)
    report[`${label}_expert_hovered`] = { ...await covers(canvasTargets),
      voice_note: await box(page.getByTestId('companion-voice-note')) }
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-expert-hovered.png`) })
    // Results, full screen.
    await idle()
    await page.getByRole('button', { name: /^Results/ }).first().click().catch(() => {})
    await page.waitForTimeout(1500)
    report[`${label}_results`] = await covers({ snapshot_bar: page.locator('.z-\\[900\\]'),
      results_toolbar_bottom: page.locator('[data-assurance-panel="results"] button').last() })
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-results.png`) })
    // Guided Hub design.
    await page.getByTestId('ui-mode-guided').click()
    await page.waitForTimeout(800)
    const hub = page.getByText('Hub design', { exact: true }).first()
    if (await hub.count()) await hub.click()
    await page.waitForTimeout(1200)
    await idle()
    report[`${label}_guided_hub`] = await covers({ ask_or_delegate: page.getByRole('button', { name: /^(Ask|Delegate)/ }),
      hub_close: page.getByRole('button', { name: /^Close/ }) })
    await page.screenshot({ path: path.join(artifacts, `launcher-${label}-guided-hub.png`) })
    await context.close()
  }
} finally {
  await browser.close()
  child.kill('SIGTERM')
  await fs.rm(storage, { recursive: true, force: true })
}
console.log(JSON.stringify(report, null, 2))
