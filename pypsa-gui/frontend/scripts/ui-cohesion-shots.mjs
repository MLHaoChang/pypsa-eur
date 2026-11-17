// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * UI-COHESION-1 audit harness: every screen of the project flow, in Guided and Expert, dark and light, for
 * one or more templates, against the real API (:8000) and Vite (:5173). Nothing is mocked.
 *
 * Per screen it writes a 1280x800 JPEG and one row of computed-style facts (font families, the share of
 * text at each size, the page background, headings, card radii), so the audit compares sections by number
 * as well as by eye.
 *
 * Usage: node scripts/ui-cohesion-shots.mjs <outDir> [templateId ...]
 *   env: CANVAS=1 (add canvas states: selected, results overlay, Satellite, Hybrid, Site 3D)  THEMES=light,dark  MODES=guided,expert  SKIP_RUN=1 (no solve)  ONLY=<substring of a screen name>  WAIT=<ms settle, default 700>
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || 'ui-cohesion-out')
const TEMPLATES = process.argv.slice(3).length ? process.argv.slice(3) : ['eh_datacenter', 'eh_microgrid']
const THEMES = (process.env.THEMES || 'light,dark').split(',')
const MODES = (process.env.MODES || 'guided,expert').split(',')
const ONLY = process.env.ONLY || ''
const SECTIONS = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial', 'scenarios', 'readiness', 'results']
// Expert slide panels (uiStore SlidePanel) and the Results tabs worth a look.
const PANELS = ['hubDesign', 'timeseries', 'horizon', 'simparams', 'scenarios', 'capacityBounds', 'issues', 'overview',
  'economics', 'gridspine', 'solveQueue', 'compare', 'reports', 'decision', 'workspace', 'settings', 'library', 'profiles']
const RESULT_TABS = ['summary', 'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions', 'adequacy', 'fmea',
  'investment', 'flows', 'loadshape', 'grid_code', 'electrical_design', 'island', 'grid_risk']

await fs.mkdir(OUT, { recursive: true })
const problems = []
const facts = []
const browser = await chromium.launch()

// Computed-style probe, run in the page. Returns what the audit compares across screens.
const probe = () => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el)
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && r.bottom > 0 && r.top < innerHeight }
  const fam = {}, size = {}, weight = {}
  let chars = 0
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent.trim(); const el = n.parentElement
    if (!t || !el || !vis(el) || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue
    const s = getComputedStyle(el)
    const f = s.fontFamily.split(',')[0].replace(/["']/g, '').trim()
    const px = Math.round(parseFloat(s.fontSize) * 10) / 10
    fam[f] = (fam[f] || 0) + t.length; size[px] = (size[px] || 0) + t.length
    weight[s.fontWeight] = (weight[s.fontWeight] || 0) + t.length; chars += t.length
  }
  const top = (o, k = 4) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, k).map(([v, c]) => `${v}:${Math.round(100 * c / Math.max(chars, 1))}%`).join(' ')
  const small = Object.entries(size).filter(([p]) => parseFloat(p) < 9).reduce((a, [, c]) => a + c, 0)
  const big = Object.entries(size).filter(([p]) => parseFloat(p) > 12).reduce((a, [, c]) => a + c, 0)
  // Page background: the largest visible element's first opaque ancestor background.
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) return c } return 'none' }
  const probeEl = document.querySelector('[data-testid^="section-view-"]') || document.querySelector('main') || document.body
  const heads = [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(vis).slice(0, 4).map(h => {
    const s = getComputedStyle(h); return `${h.tagName.toLowerCase()} "${h.textContent.trim().slice(0, 28)}" ${s.fontSize}/${s.fontWeight} ${s.color}${s.textTransform !== 'none' ? ' ' + s.textTransform : ''}`
  })
  const radii = {}
  for (const el of document.querySelectorAll('div,section,article,aside')) {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect()
    if (r.width * r.height < 4000 || !vis(el) || parseFloat(s.borderTopWidth) < 1) continue
    radii[s.borderTopLeftRadius] = (radii[s.borderTopLeftRadius] || 0) + 1
  }
  const text = document.body.innerText || ''
  return {
    families: top(fam, 3), sizes: top(size, 5), weights: top(weight, 3), under9pct: Math.round(100 * small / Math.max(chars, 1)),
    over12pct: Math.round(100 * big / Math.max(chars, 1)), pageBg: bgOf(probeEl), bodyBg: getComputedStyle(document.body).backgroundColor,
    headings: heads, cardRadii: Object.entries(radii).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([r, c]) => `${r}×${c}`).join(' '),
    chars, empty: /no results yet|not run|nothing to show|no data/i.test(text),
  }
}

async function signIn() {
  const ctx = await browser.newContext()
  const p = await ctx.newPage()
  await p.goto(BASE)
  const c = devCredentials()
  await p.locator('#email').fill(c.email)
  await p.locator('#password').fill(c.password)
  await p.getByRole('button', { name: 'Sign in', exact: true }).click()
  await p.waitForURL(/\/projects/)
  const s = await ctx.storageState()
  await ctx.close()
  return s
}

const newCtx = async (auth, project, mode, theme) => {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' })
  await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await ctx.addInitScript(({ project, mode, theme }) => {
    if (project) localStorage.setItem('network-diagram:current-project', project)
    localStorage.setItem('network-diagram:ui-mode', mode)
    localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    localStorage.setItem('network-diagram:theme', theme)
  }, { project, mode, theme })
  return ctx
}

const store = (page, fn, arg) => page.evaluate(async ({ src, arg }) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  return (new Function('s', 'a', src))(useUIStore.getState(), arg)
}, { src: fn, arg })

async function shoot(page, name, meta) {
  if (ONLY && !name.includes(ONLY)) return
  try {
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
    await page.waitForTimeout(Number(process.env.WAIT || 700))
    const f = await page.evaluate(probe)
    facts.push({ name, ...meta, ...f })
    await page.screenshot({ path: path.join(OUT, `${name}.jpg`), type: 'jpeg', quality: 72 })
  } catch (e) { problems.push(`${name}: ${e.message.split('\n')[0]}`) }
}

try {
  const auth = await signIn()

  // Sign-in and Home need no project.
  for (const theme of THEMES) {
    const ctx = await newCtx(auth, null, 'guided', theme)
    const page = await ctx.newPage()
    await page.goto(`${BASE}/projects`); await shoot(page, `home-${theme}`, { screen: 'home', theme })
    await ctx.close()
    const anon = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    // The boot script drops a stored theme unless the schema marker is '2' (THEME-LIGHT-1 T1): set both.
    await anon.addInitScript(t => { localStorage.setItem('network-diagram:theme-schema', '2'); localStorage.setItem('network-diagram:theme', t) }, theme)
    const lp = await anon.newPage(); await lp.goto(BASE); await shoot(lp, `signin-${theme}`, { screen: 'signin', theme })
    await anon.close()
  }

  for (const tpl of TEMPLATES) {
    const short = tpl.replace(/^eh_/, '')
    const project = `cohesion ${short}`
    const boot = await newCtx(auth, project, 'guided', 'light')
    const bp = await boot.newPage(); bp.setDefaultTimeout(60000)
    await bp.goto(`${BASE}/projects`); await bp.waitForLoadState('networkidle')
    const made = await bp.evaluate(async ({ id, name }) => {
      const { projectsApi } = await import('/src/api/projects.ts')
      const list = await projectsApi.list().catch(() => [])
      if (list.some(p => p.name === name)) { await projectsApi.activate(name); return 'existing' }
      await projectsApi.createFromTemplate(id, name); return 'created'
    }, { id: tpl, name: project })
    console.log(`${tpl}: ${made}`)
    if (!process.env.SKIP_RUN && !ONLY) {
      const ran = await bp.evaluate(async () => {
        const { simulationApi } = await import('/src/api/simulation.ts')
        await simulationApi.run().catch(e => { throw new Error(String(e?.response?.data?.detail ?? e.message).slice(0, 200)) })
        for (let i = 0; i < 100; i++) {
          await new Promise(r => setTimeout(r, 3000))
          const s = await simulationApi.getStatus().catch(() => null)
          if (s && !s.running) return `${s.condition} ${s.objective}`
        }
        return 'timeout'
      }).catch(e => `run failed: ${e.message}`)
      console.log(`${tpl}: run → ${ran}`)
      if (/failed|timeout/.test(ran)) problems.push(`${tpl}: ${ran}`)
    }
    await boot.close()

    for (const theme of THEMES) for (const mode of MODES) {
      const ctx = await newCtx(auth, project, mode, theme)
      const page = await ctx.newPage(); page.setDefaultTimeout(30000)
      page.on('pageerror', e => problems.push(`${short}-${mode}-${theme}: page error: ${e.message}`))
      await page.goto(`${BASE}/`); await page.waitForLoadState('networkidle')
      await page.evaluate(async name => { const { projectsApi } = await import('/src/api/projects.ts'); await projectsApi.activate(name) }, project)
      await page.reload(); await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.waitForTimeout(3000)
      const base = `${short}-${mode}-${theme}`
      if (mode === 'guided') {
        for (const [i, s] of SECTIONS.entries()) {
          await store(page, `s.setSlidePanel('hubDesign'); s.setActiveSection(a)`, s)
          await page.getByTestId(`section-view-${s}`).waitFor({ timeout: 10000 }).catch(() => problems.push(`${base}-s${i + 1}: no section-view`))
          await shoot(page, `${base}-s${String(i + 1).padStart(2, '0')}-${s}`, { screen: `guided s${i + 1} ${s}`, tpl: short, mode, theme })
        }
        for (const tab of ['summary', 'adequacy', 'fmea']) {
          await store(page, `s.requestResultsTab(a); s.setSlidePanel('results')`, tab)
          await shoot(page, `${base}-res-${tab}`, { screen: `guided results ${tab}`, tpl: short, mode, theme })
        }
      } else {
        await store(page, `s.setSlidePanel(null)`)
        await shoot(page, `${base}-canvas`, { screen: 'expert canvas', tpl: short, mode, theme })
        if (process.env.CANVAS) {
          // Canvas states (UI-COHESION-1 extension): a selected node, the results overlay, then the map and 3D modes.
          const nodes = page.locator('.react-flow__node')
          if (await nodes.count()) { await nodes.nth(Math.min(3, (await nodes.count()) - 1)).click({ force: true }).catch(() => {}); await shoot(page, `${base}-canvas-selected`, { screen: 'expert canvas selected', tpl: short, mode, theme }) }
          const res = page.getByRole('button', { name: /^Results/ }).first()
          if (await res.count()) { await res.click().catch(() => {}); await shoot(page, `${base}-canvas-results`, { screen: 'expert canvas results overlay', tpl: short, mode, theme }); await res.click().catch(() => {}) }
          for (const m of ['Satellite', 'Hybrid', 'Site 3D']) {
            const b = page.getByRole('button', { name: m, exact: true }).first()
            if (!(await b.count())) { problems.push(`${base}: no ${m} button`); continue }
            await b.click().catch(() => {}); await page.waitForTimeout(2500)
            await shoot(page, `${base}-canvas-${m.toLowerCase().replace(' ', '')}`, { screen: `expert canvas ${m}`, tpl: short, mode, theme })
          }
          await page.getByRole('button', { name: 'Blank', exact: true }).first().click().catch(() => {})
        }
        for (const p of PANELS) {
          await store(page, `s.setSlidePanel(a)`, p)
          await shoot(page, `${base}-p-${p}`, { screen: `expert panel ${p}`, tpl: short, mode, theme })
        }
        for (const tab of RESULT_TABS) {
          await store(page, `s.requestResultsTab(a); s.setSlidePanel('results')`, tab)
          await shoot(page, `${base}-res-${tab}`, { screen: `expert results ${tab}`, tpl: short, mode, theme })
        }
      }
      await ctx.close()
    }
  }
} finally {
  await fs.writeFile(path.join(OUT, 'facts.jsonl'), facts.map(f => JSON.stringify(f)).join('\n') + '\n')
  await browser.close()
}
if (problems.length) console.error(problems.join('\n'))
console.log(`${facts.length} screens in ${OUT}`)
