import { chromium } from '/opt/node-tools/node_modules/playwright/index.mjs'
import fs from 'node:fs'
export const S = new URL('.', import.meta.url).pathname
export const OUT = process.env.OUT
export async function open(mode = 'guided') {
  const b = await chromium.launch()
  const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 } })
  const ck = JSON.parse(fs.readFileSync(S + 'cookies.json'))
  await ctx.addCookies(Object.entries(ck).map(([n, value]) => ({ name: n, value, url: 'http://127.0.0.1:5173' })))
  const page = await ctx.newPage()
  page.on('pageerror', e => console.log('PAGEERROR', e.message))
  await page.goto('http://127.0.0.1:5173/app?project=8e11b37b-f97d-42fc-a485-ae1d886b6525')
  await page.getByTestId('ui-mode-' + mode).waitFor()
  await page.getByTestId('ui-mode-' + mode).click()
  await page.waitForTimeout(1500)
  return { b, page }
}
