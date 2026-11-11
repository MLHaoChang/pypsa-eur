// Screenshot the decision study's "How robust" screen (the tornado chart) over mocked study routes.
//
//   node tornado-shots.mjs <outDir> <variant: base|missing|negative|none> <theme: dark|light> <mode: expert|guided> [width]
//   -> <outDir>/s5-<variant>-<theme>-<mode>[-w<width>].png
//
// Env: BASE_URL (default http://127.0.0.1:5173) to shoot another Vite (e.g. a "before" worktree);
//      NAME overrides <variant> in the file name (NAME=before); PROJECT names the real project to open
//      (default s5-tornado, created from the eh_datacenter template when missing).
//
// The backend refuses every study route in auth mode, and the panel then shows "unavailable". So the
// script signs in for real, answers /api/health with auth_enabled:false, and serves the study routes
// from src/pages/decision/__fixtures__/*.json. Nothing is solved and no study is stored.
import { createRequire } from 'module'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
const { chromium } = createRequire(import.meta.url)('playwright')

const [outDir, variant = 'base', theme = 'dark', mode = 'expert', widthArg] = process.argv.slice(2)
if (!outDir || !['base', 'missing', 'negative', 'none'].includes(variant) || !['dark', 'light'].includes(theme) || !['expert', 'guided'].includes(mode)) {
  console.error('usage: node tornado-shots.mjs <outDir> <base|missing|negative|none> <dark|light> <expert|guided> [width]')
  process.exit(2)
}
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:5173').replace(/\/$/, '')
const PROJECT = process.env.PROJECT || 's5-tornado'
const width = Number(widthArg || 1440)
const here = path.dirname(fileURLToPath(import.meta.url))
const fx = name => JSON.parse(fs.readFileSync(path.resolve(here, '../../../../pypsa-gui/frontend/src/pages/decision/__fixtures__', `${name}.json`), 'utf8'))
const study = fx('study'), ledger = fx('ledger'), run = fx('run'), findings = fx('findings'), tornado = fx('tornado'), library = fx('library'), optionCase = fx('case')

const rob = findings.robustness
if (variant === 'missing') {
  const row = rob.tornado.find(r => r.key === 'demand_charge_price')
  row.npv_high = null; row.swing = null
} else if (variant === 'negative') {
  rob.npv_centre -= 3_500_000
  for (const r of rob.tornado) { r.npv_low -= 3_500_000; r.npv_high -= 3_500_000 }
} else if (variant === 'none') {
  rob.npv_centre = null
  for (const r of rob.tornado) { r.npv_low = null; r.npv_high = null; r.swing = null }
}
tornado.robustness = rob

const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width, height: 900 }, deviceScaleFactor: 2 })
const p = await ctx.newPage()
p.setDefaultTimeout(20000)

// Sign in, and make sure the project the study "lives in" exists.
await p.goto(`${BASE}/`)
const setup = await p.evaluate(async ({ pw, name }) => {
  const login = await fetch('/api/auth/login', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'admin@example.com', password: pw }) })
  const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
  const list = await (await fetch('/api/projects/', { credentials: 'include' })).json().catch(() => [])
  const has = Array.isArray(list) && list.some(x => x.name === name || x.id === name)
  let made = null
  if (!has) made = (await fetch(`/api/projects/from_template/eh_datacenter?name=${name}`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } })).status
  return { login: login.status, has, made }
}, { pw, name: PROJECT })
console.log('setup', JSON.stringify(setup))

await p.route('**/api/health', async route => {
  const r = await route.fetch(); const j = await r.json(); j.auth_enabled = false
  await route.fulfill({ response: r, json: j })
})
const refuse = (route, kind) => route.fulfill({ status: 404, json: { error_kind: kind, message: kind } })
await p.route(/\/api\/projects\/[^/]+\/studies(\/.*)?$/, route => {
  const req = route.request()
  const tail = new URL(req.url()).pathname.replace(/^\/api\/projects\/[^/]+\/studies\/?/, '').replace(/^[^/]+\/?/, m => (m.startsWith('library') || m.startsWith('preview') ? m : 'ID/'))
  if (req.method() !== 'GET') return route.fulfill({ status: 409, json: { error_kind: 'mocked', message: 'This screen is served from fixtures.' } })
  if (tail === '') return route.fulfill({ json: [study] })
  if (tail === 'library') return route.fulfill({ json: library })
  if (tail === 'ID/' || tail === 'ID') return route.fulfill({ json: study })
  if (tail === 'ID/ledger') return route.fulfill({ json: ledger })
  if (tail === 'ID/run') return route.fulfill({ json: run })
  if (tail === 'ID/findings') return route.fulfill({ json: findings })
  if (tail === 'ID/findings/tornado') return route.fulfill({ json: tornado })
  if (tail === 'ID/report') return refuse(route, 'report_never_assembled')
  if (/^ID\/options\/[^/]+\/case$/.test(tail)) return route.fulfill({ json: optionCase })
  return refuse(route, 'not_mocked')
})
await p.addInitScript(ref => { try { localStorage.setItem('network-diagram:decision-study', JSON.stringify(ref)) } catch { /* no storage */ } },
  { project: PROJECT, studyId: study.study_id })

// Projects home -> the project -> mode and theme -> the decision panel -> the robustness view.
await p.goto(`${BASE}/`); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${PROJECT}(?!\\w)`) }).first().click({ timeout: 8000 }); await p.waitForTimeout(4000)
await p.getByRole('button', { name: mode === 'guided' ? 'Guided' : 'Expert', exact: true }).first().click(); await p.waitForTimeout(1200)
const themeBtn = p.locator('button[title^="Switch to"][title$="theme"]').first()
if ((await themeBtn.getAttribute('title')).toLowerCase().includes(theme)) { await themeBtn.evaluate(el => el.click()); await p.waitForTimeout(900) }
if (!(await p.getByTestId('decision-study').count())) {
  // Expert: the sidebar's "Decision study" row (shown in the study's own project).
  // Guided has no such row: Projects home -> "Your decision studies" -> the study.
  const row = p.getByRole('button', { name: 'Decision study', exact: true }).first()
  const viaRow = mode === 'expert' && await row.waitFor({ timeout: 8000 }).then(() => true, () => false)
  if (viaRow) await row.click()
  else {
    console.log('entry: projects home')
    await p.goto(`${BASE}/`)
    await p.getByTestId('decision-studies-entry').getByRole('button', { name: new RegExp(study.name) }).first().click({ timeout: 8000 })
  }
}
await p.getByTestId('decision-study').waitFor()
if (mode === 'guided') await p.getByTestId('decision-rail-improve').click()
else await p.getByRole('navigation', { name: 'Decision study sections' }).getByRole('button', { name: 'How robust', exact: true }).click()

// The chart is `[data-testid="tornado-chart"]` on the new screen; the old screen has no such node.
const chart = p.getByTestId('tornado-chart')
const hasChart = await chart.first().waitFor({ timeout: 8000 }).then(() => true, () => false)
if (hasChart) {
  let box = await chart.boundingBox()
  if (box.height + 200 > 900) await p.setViewportSize({ width, height: Math.ceil(box.height + 260) })
  await chart.evaluate(el => {
    el.scrollIntoView({ block: 'center' })
    // Its end would sit under the chat launcher: scroll to the end, where the page leaves room for it.
    if (el.getBoundingClientRect().bottom > innerHeight - 170) {
      for (let n = el.parentElement; n; n = n.parentElement) if (n.scrollHeight > n.clientHeight + 4) n.scrollTop = n.scrollHeight
    }
  }); await p.waitForTimeout(900)
  box = await chart.boundingBox()
  const vp = p.viewportSize()
  console.log('chart box', JSON.stringify(box), 'in view', box.y >= 0 && box.y + box.height <= vp.height && box.x >= 0 && box.x + box.width <= vp.width)
  console.log('launcher', await p.evaluate(() => { const l = document.querySelector('.companion-launcher'); const c = document.querySelector('.results-scroll-clear'); return JSON.stringify({ l: l && l.getBoundingClientRect(), pad: c && getComputedStyle(c).paddingBottom }) }))
  console.log('bars', await p.getByTestId('tornado-bar').count())
  console.log('overflow-x', await chart.evaluate(el => {
    const out = []
    const c = el.getBoundingClientRect()
    for (const n of el.querySelectorAll('*')) { const r = n.getBoundingClientRect(); if (r.width && (r.right > c.right + 1 || r.left < c.left - 1)) out.push(`${n.tagName}:${(n.textContent || '').slice(0, 40)}`) }
    return out.slice(0, 8)
  }))
  console.log('text', (await chart.innerText()).replace(/\s+/g, ' '))
} else {
  console.log('no [data-testid="tornado-chart"] on this build')
  await p.waitForTimeout(1500)
}
fs.mkdirSync(outDir, { recursive: true })
const file = `${outDir}/s5-${process.env.NAME || variant}-${theme}-${mode}${widthArg ? `-w${width}` : ''}.png`
await p.screenshot({ path: file })
console.log('shot', file)
await b.close()
