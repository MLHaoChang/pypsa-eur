// node shoot.cjs <outdir>: shoots screens.html (3 screens x light/dark).
const { chromium } = require('playwright')
const { pathToFileURL } = require('node:url')
const { resolve } = require('node:path')
;(async () => {
  const out = process.argv[2] ?? 'shots'
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
  const url = pathToFileURL(resolve(__dirname, 'screens.html')).href
  for (const theme of ['light', 'dark']) for (const screen of ['workbench', 'home', 'results']) {
    await page.goto(`${url}?theme=${theme}&screen=${screen}`)
    await page.screenshot({ path: `${out}/${screen}-${theme}.png` })
  }
  await browser.close()
})()
