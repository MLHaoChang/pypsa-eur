// Open a project in the UI (so it is the session's active project), then call the API.
//   node s6-api.mjs <state.json> <project> "<METHOD> <path> [json body]" …     (env BASE_URL; OUTLEN = chars printed per reply)
import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
const [state, project, ...calls] = process.argv.slice(2)
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: state })
const p = await ctx.newPage()
await p.goto((process.env.BASE_URL || 'http://127.0.0.1:5173') + '/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click({ timeout: 8000 }); await p.waitForTimeout(4500)
for (const c of calls) {
  const [method, url, ...rest] = c.split(' ')
  const r = await p.evaluate(async ({ method, url, body }) => {
    const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
    const r = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body || undefined })
    return [r.status, await r.text()]
  }, { method, url, body: rest.join(' ') })
  console.log(method, url, r[0], r[1].slice(0, Number(process.env.OUTLEN || 400)))
}
await b.close()
