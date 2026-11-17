// GC-09 shots: the Grid compliance (TRGC) section on Grid study, Expert mode.
// Needs the seeded backend on :8010 and Vite on :5180 (run.sh starts both).
//   NODE_PATH=$(npm root -g) PWFILE=<app-data>/dev-admin-password OUT=<dir> node grid-compliance-shots.mjs
// THEME=light|dark picks the theme (default: the app's). Writes collapsed.png, opened-11.png, out-of-date-12.png, freeze-off-14.png, data-15.png.
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const { chromium } = createRequire(import.meta.url)('playwright')
const BASE = process.env.BASE ?? 'http://127.0.0.1:5180'
const PROJECT = process.env.GC_SHOTS_PROJECT ?? 'Harbour Hub'
const OUT = process.env.OUT ?? '.'
const pw = fs.readFileSync(process.env.PWFILE, 'utf8').trim()
fs.mkdirSync(OUT, { recursive: true })

const b = await chromium.launch()
try {
  const p = await b.newPage({ viewport: { width: 1440, height: 2400 }, deviceScaleFactor: 2 })
  await p.goto(`${BASE}/`)
  await p.getByLabel('Email').fill('admin@example.com')
  await p.getByLabel('Password').fill(pw)
  await p.getByRole('button', { name: 'Sign in' }).click()
  await p.getByRole('link', { name: new RegExp(PROJECT) }).first().click()
  await p.getByRole('button', { name: 'Expert', exact: true }).click()
  // THEME=light|dark: the status-bar toggle names the current theme; flip it when it differs.
  if (process.env.THEME) {
    const toggle = p.getByRole('button', { name: /^(light|dark)$/ }).first()
    if ((await toggle.innerText()).trim() !== process.env.THEME) await toggle.click()
  }
  await p.getByRole('button', { name: 'Grid study', exact: true }).click()
  const title = p.getByText('Grid compliance (TRGC)', { exact: true })
  await title.waitFor({ timeout: 30000 })
  const section = p.locator('section', { has: title }).last()
  await section.getByTestId('gc-row-01').waitFor()
  await p.getByText('Expert mode on').waitFor({ state: 'hidden', timeout: 15000 }).catch(() => {})
  await p.waitForTimeout(800)
  const shot = async (name, loc = section) => {
    await loc.scrollIntoViewIfNeeded()
    await loc.screenshot({ path: path.join(OUT, name), animations: 'disabled' })
    console.log(path.join(OUT, name))
  }
  await shot('collapsed.png')
  for (const [code, name] of [['11', 'opened-11.png'], ['12', 'out-of-date-12.png'], ['14', 'freeze-off-14.png'], ['15', 'data-15.png']]) {
    const row = section.getByTestId(`gc-row-${code}`)
    await row.getByRole('button', { expanded: false }).first().click()
    await p.waitForTimeout(500)
    await shot(name, row)
    await row.getByRole('button', { expanded: true }).first().click()
  }
} finally {
  await b.close()
}
