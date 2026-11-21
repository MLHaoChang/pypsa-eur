// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * template-e2e 06 screenshot harness: the reliability goal's allowed-shortfall
 * chip (Assumed for the site type, Stated once typed, Confirmed after "Keep all
 * defaults") in Guided's Goal card and Expert's Solver settings goal block, dark
 * and light, on the real app with a fresh eh_datacenter project. No mocked
 * routes. Prints the WCAG contrast of each chip label against its composited
 * background and records a problem below 4.5:1.
 * Needs the API on :8000 and Vite on :5173 (dev admin, scripts/dev-credentials.mjs).
 *
 * Usage: node scripts/goal-assumed-shots.mjs [outDir]      env: BASE
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const here = path.dirname(fileURLToPath(import.meta.url))
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(here, '..', '..', '..', '.scratch', 'template-e2e', 'shots-06'))
const PROJECT = 'Goal assumed shots'
const TEMPLATE = 'eh_datacenter'
const KEY = {
  assumed: 'Assumed for this site type. Type a value to set your own.',
  typed: 'Typed by you.',
  confirmed: 'You kept this default. Type a value to set your own.',
}

const problems = []
const written = []
const ratios = []
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()

/** WCAG contrast of the chip label (first span of the chip) against its
 *  background, every ancestor's background alpha-composited from the page down. */
async function chipContrast(chip) {
  return chip.evaluate(el => {
    const label = el.querySelector('span')
    const cv = document.createElement('canvas'); cv.width = cv.height = 1
    const cx = cv.getContext('2d', { willReadFrequently: true })
    const rgba = css => {
      cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = css; cx.fillRect(0, 0, 1, 1)
      const [r, g, b, a] = cx.getImageData(0, 0, 1, 1).data
      return a === 0 ? [0, 0, 0, 0] : [r, g, b, a / 255]
    }
    const over = (top, under) => [0, 1, 2].map(i => top[i] * top[3] + under[i] * (1 - top[3])).concat(1)
    const chain = []
    for (let n = label; n; n = n.parentElement) chain.push(rgba(getComputedStyle(n).backgroundColor))
    let bg = [255, 255, 255, 1]
    for (const c of chain.reverse()) if (c[3] > 0) bg = over(c, bg)
    const fg = over(rgba(getComputedStyle(label).color), bg)
    const lum = c => {
      const [r, g, b] = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    const hex = c => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('')
    return { ratio: (a + 0.05) / (b + 0.05), text: label.textContent, fg: hex(fg), bg: hex(bg) }
  })
}

let auth
try {
  const owner = await browser.newContext()
  const login = await owner.newPage()
  const c = await devCredentials()
  await login.goto(`${BASE}/login`)
  await login.locator('#email').fill(c.email)
  await login.locator('#password').fill(c.password)
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await login.waitForURL(/\/projects/)
  // A fresh project every run: the Confirmed step acknowledges the defaults for good.
  const made = await login.evaluate(async ({ tid, name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    const list = await projectsApi.list().catch(() => [])
    if (list.some(p => p.name === name)) {
      try { await projectsApi.delete(name, true) } catch (e) { return `failed: delete: ${e.message}` }
    }
    try { await projectsApi.createFromTemplate(tid, name); return 'made' } catch (e) { return `failed: ${e.message}` }
  }, { tid: TEMPLATE, name: PROJECT })
  if (made.startsWith('failed')) problems.push(`create: ${made}`)
  // Load it on the server before any shot tab opens, so no tab's save meets an empty network (409 toast).
  const loaded = await login.evaluate(async ({ name }) => {
    const { projectsApi } = await import('/src/api/projects.ts')
    try { await projectsApi.load(name); return 'ok' } catch (e) { return `failed: ${e.message}` }
  }, { name: PROJECT })
  if (loaded !== 'ok') problems.push(`load: ${loaded}`)
  auth = await owner.storageState()
  await owner.close()

  /** A tab in `mode` and `theme`, on the project, autosave off. */
  async function open(mode, theme) {
    const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1440, height: 2400 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ theme, project, mode }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', theme)
      localStorage.setItem('network-diagram:autosave', 'false')
    }, { theme, project: PROJECT, mode })
    const page = await ctx.newPage()
    page.setDefaultTimeout(20000)
    page.on('pageerror', e => problems.push(`${mode}/${theme}: page error: ${e.message}`))
    page.on('response', r => { if (r.status() === 409) problems.push(`${mode}/${theme}: 409 on ${r.url()}`) })
    await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.getByText(PROJECT).first().waitFor(); await page.waitForTimeout(3000)
    return { ctx, page }
  }

  /** The goal block through the app's own link (Guided: Goal card; Expert: Solver settings › Network). */
  async function openGoal(page, mode) {
    await page.evaluate(async () => {
      const { openReliabilityGoal } = await import('/src/layout/flowNavigation.ts')
      openReliabilityGoal()
    })
    const block = mode === 'guided' ? page.getByTestId('hub-card-goal') : page.locator('#solver-reliability-goal')
    await block.waitFor()
    await page.getByTestId(`${mode === 'guided' ? 'hub' : 'solver'}-goal-lole`).waitFor()
    await page.waitForTimeout(800)
    return block
  }

  async function check(page, block, mode, theme, state, value) {
    const p = mode === 'guided' ? 'hub' : 'solver'
    const tag = `${mode}/${theme}/${state}`
    const got = await page.getByTestId(`${p}-goal-lole`).inputValue()
    if (got !== value) problems.push(`${tag}: ${p}-goal-lole value '${got}', expected '${value}'`)
    const chip = page.getByTestId(`${p}-goal-lole-assumed`)
    const ds = await chip.getAttribute('data-state').catch(() => null)
    if (ds !== state) problems.push(`${tag}: ${p}-goal-lole-assumed data-state '${ds}', expected '${state}'`)
    const key = ((await page.getByTestId(`${p}-goal-lole-key`).textContent().catch(() => null)) ?? '').trim()
    if (key !== KEY[state]) problems.push(`${tag}: ${p}-goal-lole-key '${key}', expected '${KEY[state]}'`)
    if (state === 'confirmed') {
      const src = ((await page.getByTestId(`${p}-goal-lole-assumed-source`).textContent().catch(() => null)) ?? '').trim()
      if (src !== 'Site type: 3 h/yr') problems.push(`${tag}: ${p}-goal-lole-assumed-source '${src}', expected 'Site type: 3 h/yr'`)
    }
    if (await block.getByText('No goal yet').count()) problems.push(`${tag}: block shows "No goal yet"`)
    if (await chip.count()) {
      const c = await chipContrast(chip)
      ratios.push(`${tag}: '${c.text}' ${c.fg} on ${c.bg} = ${c.ratio.toFixed(2)}:1`)
      if (c.ratio < 4.5) problems.push(`${tag}: chip label contrast ${c.ratio.toFixed(2)}:1 < 4.5 (${c.fg} on ${c.bg})`)
    }
    await block.scrollIntoViewIfNeeded()
    const file = path.join(OUT, `te2e-06-goal-${mode}-${state}-${theme}.png`)
    await block.screenshot({ path: file }); written.push(file)
  }

  async function shoot(mode, theme, fn) {
    const { ctx, page } = await open(mode, theme)
    try { await fn(page) } catch (e) {
      problems.push(`${mode}/${theme}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${theme}.png`) }).catch(() => {})
    }
    await ctx.close()
  }

  // Assumed, then Typed (not saved), in both homes and themes.
  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    await shoot(mode, theme, async page => {
      const block = await openGoal(page, mode)
      await check(page, block, mode, theme, 'assumed', '3')
      const input = page.getByTestId(`${mode === 'guided' ? 'hub' : 'solver'}-goal-lole`)
      await input.fill('2'); await page.waitForTimeout(500)
      await check(page, block, mode, theme, 'typed', '2')
    })
  }

  // Confirmed: "Keep all defaults" on Guided's Assumptions card in the goal's section (changes the project).
  await shoot('guided', 'light', async page => {
    await openGoal(page, 'guided')
    const card = page.getByTestId('assumptions-card')
    await card.waitFor()
    await card.getByTestId('assumptions-accept-all').click()
    await page.getByTestId('assumptions-kept').waitFor()
  })
  for (const mode of ['guided', 'expert']) for (const theme of ['light', 'dark']) {
    await shoot(mode, theme, async page => {
      const block = await openGoal(page, mode)
      await check(page, block, mode, theme, 'confirmed', '3')
    })
  }
} finally {
  await browser.close()
}
for (const r of ratios) console.log(r)
for (const w of written) console.log(w)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
