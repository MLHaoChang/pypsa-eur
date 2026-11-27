/** Shared helpers for the template end-to-end journeys (E2E-TEMPLATES-1).
 * Boots the isolated loopback backend (composer-fixture.py, no model), signs in
 * through the real login form and offers the small set of UI moves every
 * journey uses. Every decision goes through the UI; the API is used only to
 * delete a project before a retry and to read the study status.
 * Gap handling: `gap(ctx, 'NN', 'what', fn)` runs fn. A failing fn is an
 * expected failure keyed to the gap issue (.scratch/template-e2e/issues/NN-*);
 * a passing fn FAILS the journey ("gap NN is fixed, drop the marker"), so a fix
 * flips the step from expected-fail to a normal assertion.
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

export const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const repository = path.resolve(frontend, '../..')
const { chromium } = createRequire(process.env.PLAYWRIGHT_MODULE_PATH || import.meta.url)('playwright')
export const python = process.env.PYPSA_COMPOSER_PYTHON || path.join(repository, '.pixi/envs/test/bin/python')

export async function tmpdir(prefix) { return fs.mkdtemp(path.join(os.tmpdir(), prefix)) }

export function startBackend(storage) {
  const child = spawn(python, [path.join(frontend, 'scripts/composer-fixture.py'), '--storage', storage, '--no-model'],
    { cwd: repository, stdio: ['ignore', 'pipe', 'pipe'] })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timed out')), 180000)
    let output = ''
    child.stdout.on('data', chunk => {
      output += chunk
      const line = output.split('\n').find(value => value.startsWith('{"base":'))
      if (line) { clearTimeout(timer); resolve({ child, ...JSON.parse(line) }) }
    })
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}`)) })
  })
}

export async function stopBackend(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 12000)
  await exited
  clearTimeout(timer)
}

export async function openBrowser() {
  return chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined })
}

export async function signIn(context, page, base, credentialsFile) {
  const credentials = JSON.parse(await fs.readFile(credentialsFile, 'utf8'))
  await page.goto(base)
  await page.locator('#email').fill(credentials.email)
  await page.locator('#password').fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
}

/** One journey context: the page, a step log and the expected-fail ledger. */
export function newContext({ page, context, base, out, template, mode }) {
  return { page, context, base, out, template, mode, steps: [], skips: [], added: {}, xfails: [], unexpected: [], problems: [] }
}

export async function step(ctx, name, fn) {
  const started = Date.now()
  try {
    await fn()
    ctx.steps.push({ name, ok: true, ms: Date.now() - started })
  } catch (error) {
    ctx.steps.push({ name, ok: false, ms: Date.now() - started, error: String(error.message || error).split('\n')[0] })
    await shot(ctx, `FAIL-${name}`)
    throw error
  }
}

/** A step the template does not take, with the reason: written to the report, never a silent pass. */
export function skip(ctx, what, why) { ctx.skips.push({ what, why }) }

export async function gap(ctx, id, what, fn) {
  let passed = false
  try { await fn(); passed = true } catch { /* expected: the gap is still open */ }
  if (passed) {
    ctx.unexpected.push({ id, what })
    ctx.steps.push({ name: `gap ${id}: ${what}`, ok: false, ms: 0, error: `gap ${id} is fixed; remove its expected-fail marker` })
  } else {
    ctx.xfails.push({ id, what })
    ctx.steps.push({ name: `gap ${id}: ${what}`, ok: true, xfail: true, ms: 0 })
  }
}

export async function shot(ctx, name) {
  try { await ctx.page.screenshot({ path: path.join(ctx.out, `${ctx.template}-${name.replace(/[^\w.-]+/g, '_')}.png`) }) } catch { /* best effort */ }
}

export async function api(ctx, method, url, data) {
  const csrf = (await ctx.context.cookies()).find(cookie => cookie.name === 'pypsa_gui_csrf')?.value
  return ctx.context.request.fetch(ctx.base + url, { method, headers: csrf ? { 'X-CSRF-Token': csrf } : {}, ...(data === undefined ? {} : { data }) })
}

/** The Guided hub panel; reopens it when a drawer or a jump to Expert closed it. Never press Escape. */
export async function ensurePanel(ctx) {
  const { page } = ctx
  const panel = page.getByTestId('hub-design-panel')
  if (await panel.isVisible().catch(() => false)) return panel
  await page.getByRole('group', { name: 'Interface mode' }).getByRole('button', { name: 'Guided', exact: true }).click()
  if (!await panel.isVisible().catch(() => false)) await page.getByTestId('sidebar-hub-design').click()
  await panel.waitFor()
  return panel
}

/** A drawer or an Expert jump can leave overlays over the rail; a reload keeps the project and clears them. */
export async function resetOverlays(ctx) {
  await ctx.page.reload()
  await ensurePanel(ctx)
}

export async function goSection(ctx, label) {
  await ensurePanel(ctx)
  const rail = ctx.page.getByRole('navigation', { name: 'Project flow sections' })
  await rail.getByRole('button', { name: new RegExp(`^\\s*(\\d+\\s*)?${label}`) }).first().click()
}

export function esc(text) { return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }

/** New project → From template → card; waits for the toast and /app. Returns the project name. */
export async function createFromTemplate(ctx, cardName, { form } = {}) {
  const { page } = ctx
  await page.getByRole('button', { name: 'New project' }).first().click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'From template', exact: true }).click()
  await dialog.getByRole('button', { name: new RegExp(`^${esc(cardName)}${cardName.includes('(staged') ? '' : '(?! \\(staged)'}`) }).first().click()
  if (form) await form(page)
  await page.getByText(/Created '.+' from template/).first().waitFor({ timeout: 180000 })
  await page.waitForURL(/\/app/, { timeout: 60000 })
  const toast = await page.getByText(/Created '.+' from template/).first().innerText()
  return toast.match(/Created '(.+?)'/)[1]
}

export async function chooseMode(ctx, mode) {
  await ctx.page.getByRole('group', { name: 'Interface mode' }).getByRole('button', { name: mode, exact: true }).click()
}

export async function report(ctx) {
  const file = path.join(ctx.out, `${ctx.template}-${ctx.mode}.json`)
  await fs.writeFile(file, JSON.stringify({ template: ctx.template, mode: ctx.mode, runSeconds: ctx.runSeconds, steps: ctx.steps, skips: ctx.skips, added: ctx.added, xfails: ctx.xfails, unexpected: ctx.unexpected }, null, 1))
  return file
}

export { assert }
