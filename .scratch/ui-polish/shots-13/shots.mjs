// ui-polish 13 shots: one Back control on a grid-study jump (Guided + Expert), and the header Back without a jump.
// Needs API :8000 and Vite :5173. Run from pypsa-gui/frontend: node ../../.scratch/ui-polish/shots-13/shots.mjs
import path from 'node:path'
import { createRequire } from 'node:module'
import { devCredentials } from '/home/user/pypsa-eur/pypsa-gui/frontend/scripts/dev-credentials.mjs'
const { chromium } = createRequire('/home/user/pypsa-eur/pypsa-gui/frontend/')('playwright')
const BASE = 'http://127.0.0.1:5173', OUT = path.dirname(new URL(import.meta.url).pathname), NAME = 'UP13 data centre'
const browser = await chromium.launch()
const c = devCredentials()
const o = await browser.newContext(); const lp = await o.newPage()
await lp.goto(BASE); await lp.locator('#email').fill(c.email); await lp.locator('#password').fill(c.password)
await lp.getByRole('button', { name: 'Sign in', exact: true }).click(); await lp.waitForURL(/\/projects/); await lp.waitForLoadState('networkidle')
console.log(await lp.evaluate(async ({ name }) => {
  const { projectsApi } = await import('/src/api/projects.ts')
  const l = await projectsApi.list().catch(() => []); if (l.some(x => x.name === name)) await projectsApi.delete(name, true)
  try { await projectsApi.createFromTemplate('eh_datacenter', name, { dc_load: {} }); return 'made' } catch (e) { return e.message }
}, { name: NAME }))
const auth = await o.storageState(); await o.close()
for (const mode of ['guided', 'expert']) {
  const ctx = await browser.newContext({ storageState: auth, viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' })
  await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
  await ctx.addInitScript(({ mode, project }) => {
    localStorage.setItem('network-diagram:current-project', project); localStorage.setItem('network-diagram:ui-mode', mode)
    localStorage.setItem('network-diagram:ui-mode-explicit', '1'); localStorage.setItem('network-diagram:theme', 'dark')
  }, { mode, project: NAME })
  const page = await ctx.newPage(); page.setDefaultTimeout(30000)
  await page.goto(BASE); await page.getByText('Open project').first().waitFor()
  await page.getByRole('link', { name: new RegExp(NAME) }).or(page.getByRole('button', { name: new RegExp(NAME) })).last().click()
  await page.waitForTimeout(3500)
  // without a jump: header Back to Results (Guided only shows it)
  await page.evaluate(async () => { (await import('/src/store/uiStore.ts')).useUIStore.getState().setSlidePanel('campusStudy') })
  await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/${mode}-no-jump.png` })
  console.log(mode, 'no-jump Back to Results:', await page.getByRole('button', { name: /Back to Results/ }).count(), 'flow-return:', await page.getByTestId('flow-return').count())
  // with a jump from the cards / canvas
  await page.evaluate(async () => {
    const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel(null)
  })
  await page.waitForTimeout(800)
  await page.evaluate(async () => {
    const { withReturnFrom } = await import('/src/layout/flowNavigation.ts'); const { useUIStore } = await import('/src/store/uiStore.ts')
    withReturnFrom(() => useUIStore.getState().setSlidePanel('campusStudy'))
  })
  await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/${mode}-jump.png` })
  console.log(mode, 'jump Back to Results:', await page.getByRole('button', { name: /Back to Results/ }).count(), 'flow-return:', await page.getByTestId('flow-return').allTextContents())
  await ctx.close()
}
await browser.close()
