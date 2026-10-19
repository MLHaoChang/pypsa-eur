/** Owner decision Q4 in the built app against the authenticated native backend.
 * With a model ready, a former local shortcut goes to the copilot; with no
 * model, the local project picker still opens and nothing reaches a model.
 * npm run build
 * PYPSA_COMPOSER_PYTHON=<test-env-python> CHROMIUM_BIN=<chromium> node scripts/smoke-companion-q4.mjs
 * The "model" is composer-fixture.py's loopback boundary; no paid inference.
 * Artifacts omit credentials/cookies.
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
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'companion-q4-'))

function fixture(storage, extra) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, ...extra],
    { cwd: repository, stdio: ['ignore', 'pipe', 'pipe'] })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timed out')), 90000)
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

// Q4 routing depends on readiness, so every load waits for the real answer.
async function load(page, navigate) {
  const readiness = page.waitForResponse(r => r.url().includes('/api/chat/health') && r.ok())
  const profiles = page.waitForResponse(r => r.url().includes('/api/chat/profiles'))
  await navigate()
  const [answered] = await Promise.all([readiness, profiles])
  page.__chatReady = (await (await page.context().request.get(answered.url())).json()).chat_ready
}

const CHECKING = 'Still checking the assistant. Nothing was sent; try again in a moment.'

// Sends once; while readiness is still settling the dock sends nothing and
// asks to try again, so retry (bounded) exactly as that caption asks.
async function say(page, text) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await page.evaluate(() => { window.__q4 = { picker: 0, utterances: [] } })
    const input = page.getByTestId('companion-live-input')
    if (!await input.isVisible()) await page.getByTestId('companion-live').click()
    await input.fill(text)
    await page.getByTestId('companion-live-send').click()
    await page.waitForTimeout(1500)
    const heard = {
      ...await page.evaluate(() => window.__q4),
      caption: await page.getByTestId('companion-live-caption').count()
        ? await page.getByTestId('companion-live-caption').textContent() : '',
    }
    if (heard.caption !== CHECKING) return { ...heard, checkingRetries: attempt }
    assert.equal(heard.picker, 0)
    assert.deepEqual(heard.utterances, [])
  }
  throw new Error('Readiness never settled')
}

async function scenario(browser, name, extra, check) {
  const storage = path.join(artifacts, name)
  const { child, base, credentials_file: file } = await fixture(storage, extra)
  try {
    const credentials = JSON.parse(await fs.readFile(file, 'utf8'))
    const context = await browser.newContext()
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
    const created = await context.request.post(base + '/api/projects/Q4 fixture', { headers: { 'X-CSRF-Token': csrf } })
    assert(created.ok(), `Native project creation failed: ${created.status()}`)
    await context.route(url => ['http:', 'https:'].includes(url.protocol) && url.hostname !== '127.0.0.1', route => route.abort())
    await page.addInitScript(() => {
      localStorage.setItem('network-diagram:current-project', 'Q4 fixture')
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      window.__q4 = { picker: 0, utterances: [] }
      window.addEventListener('chat:open-project-picker', () => { window.__q4.picker += 1 })
      window.addEventListener('companion:live-utterance', ev => { window.__q4.utterances.push(ev.detail) })
    })
    await load(page, () => page.goto(base + '/app'))
    await page.getByTestId('companion-live').waitFor()
    const outcome = await check(page, storage)
    assert.deepEqual(errors, [])
    await context.close()
    return outcome
  } finally {
    await stop(child)
    await fs.rm(storage, { recursive: true, force: true })
  }
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
const results = []
try {
  results.push(await scenario(browser, 'with-model', [], async (page, storage) => {
    const marker = path.join(storage, 'unexpected-provider-call')
    const out = {}
    for (const text of ['Open the project', 'Walk me through the key results in each tab']) {
      await fs.rm(marker, { force: true })
      const heard = await say(page, text)
      assert.deepEqual(heard.utterances, [text], `${text}: forwarded once to the copilot; saw ${JSON.stringify(heard)}`)
      assert.equal(heard.picker, 0, `${text}: no local picker`)
      assert.doesNotMatch(heard.caption, /Opening the project list|Waiting for matching/)
      // The real backend called the configured model boundary for this sentence.
      await fs.access(marker)
      out[text] = { forwarded: heard.utterances.length, picker: heard.picker, modelCalled: true,
        checkingRetries: heard.checkingRetries }
      await load(page, () => page.reload())
      await page.getByTestId('companion-live').waitFor()
    }
    assert.equal(page.__chatReady, true)
    return { scenario: 'with-model', chatReady: page.__chatReady, ...out }
  }))
  results.push(await scenario(browser, 'no-model', ['--no-model'], async (page, storage) => {
    const picker = await say(page, 'Open the project')
    assert.equal(picker.picker, 1, `the local picker opens; saw ${JSON.stringify({ ...picker, chatReady: page.__chatReady })}`)
    assert.deepEqual(picker.utterances, [])
    assert.equal(picker.caption, 'Opening the project list.')
    // The real Open project dialog rendered; close it before the next sentence.
    const title = page.getByText('Open project', { exact: true })
    await title.waitFor()
    await title.locator('..').getByRole('button', { name: 'Close' }).click()
    await title.waitFor({ state: 'detached' })
    const general = await say(page, 'List the generators')
    assert.deepEqual(general.utterances, [])
    assert.match(general.caption, /Add an API key/)
    await assert.rejects(fs.access(path.join(storage, 'unexpected-provider-call')))
    assert.equal(page.__chatReady, false)
    return { scenario: 'no-model', chatReady: page.__chatReady, picker: picker.picker, generalForwarded: general.utterances.length }
  }))
} finally {
  await browser.close()
}
console.log(JSON.stringify({ passed: results.length, results }, null, 2))
await fs.rm(artifacts, { recursive: true, force: true })
