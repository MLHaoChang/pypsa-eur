// UX walkthrough 07: the skip link (first Tab stop, real app) and the focused choice card
// (component harness), light and dark. Needs Vite on BASE; the app needs no sign-in in local mode.
// The choice card renders from scripts/focus-harness/main.tsx, imported into the signed-in page.
//   BASE=http://127.0.0.1:5173 OUT=<dir> node scripts/focus-landmarks-shots.mjs
import fs from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { devCredentials } from './dev-credentials.mjs'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)('playwright')
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS_SH = path.resolve(HERE, '../../../scripts/coord/shots.sh')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = process.env.OUT || path.resolve('focus-landmarks-shots')
await fs.mkdir(OUT, { recursive: true })
const browser = await chromium.launch()
// Sign in once (Vite gates every page behind the session) and keep the cookies for both themes.
const setup = await browser.newContext({ viewport: { width: 1280, height: 800 } })
{
  const page = await setup.newPage()
  page.setDefaultTimeout(60000)
  const { email, password } = devCredentials()
  await page.goto(BASE)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects/)
  await page.waitForLoadState('networkidle')
  // A blank project, so the app shell (not the Projects page) loads.
  await page.evaluate(async name => {
    const { networkApi } = await import('/src/api/network.ts')
    const { projectsApi } = await import('/src/api/projects.ts')
    await networkApi.resetNetwork()
    await projectsApi.save(name, true)
  }, 'focus shots blank')
}
const state = await setup.storageState()
await setup.close()
let failed = false
for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme, deviceScaleFactor: 1, storageState: state })
  await ctx.addInitScript(t => { try { localStorage.setItem('theme', t) } catch {} }, theme)
  const page = await ctx.newPage()
  await page.goto(`${BASE}/app?project=focus%20shots%20blank`)
  await page.waitForSelector('[data-testid="skip-to-main"]', { state: 'attached', timeout: 30000 })
  await page.waitForLoadState('networkidle')
  await page.evaluate(() => { document.activeElement?.blur?.(); return import('/scripts/focus-harness/main.tsx') })
  await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme)
  await page.waitForSelector('[data-testid="chat-choice-card"]')
  await page.waitForTimeout(500)
  const focused = await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))
  if (focused !== 'chat-choice-card') { console.error(`${theme}: choice card not focused (${focused})`); failed = true }
  await page.screenshot({ path: path.join(OUT, `choice-card-${theme}.png`), clip: { x: 0, y: 0, width: 420, height: 260 } })
  await page.goto(`${BASE}/app?project=focus%20shots%20blank`)
  await page.waitForSelector('[data-testid="skip-to-main"]', { state: 'attached', timeout: 30000 })
  await page.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme)
  await page.keyboard.press('Tab')
  const first = await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))
  if (first !== 'skip-to-main') { console.error(`${theme}: first Tab stop is ${first}`); failed = true }
  await page.screenshot({ path: path.join(OUT, `skip-link-${theme}.png`), clip: { x: 0, y: 0, width: 640, height: 200 } })
  await ctx.close()
}
await browser.close()
for (const f of await fs.readdir(OUT)) if (/^(choice-card|skip-link)-\w+\.png$/.test(f)) console.log(execFileSync(SHOTS_SH, [path.join(OUT, f)]).toString().trim())
process.exit(failed ? 1 : 0)
