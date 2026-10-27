// LC-02 plan-card shots (PR 792): built app, real login/project (composer-fixture oj21),
// chat stream fulfilled with tool_result frames whose _task views come from assistant_tasks._view.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
const repo = '/home/user/pypsa-eur'
const { chromium } = createRequire(repo + '/pypsa-gui/frontend/package.json')('playwright')
const out = process.argv[2]; const views = JSON.parse(await fs.readFile(process.argv[3], 'utf8'))
await fs.mkdir(out, { recursive: true })
const storage = await fs.mkdtemp('/tmp/claude-0/lc02-store-')
const child = spawn('/root/.venv-pypsa-gui/bin/python3', [repo + '/pypsa-gui/frontend/scripts/composer-fixture.py', '--storage', storage, '--journey', 'oj21'], { cwd: repo, stdio: ['ignore', 'pipe', 'inherit'] })
const ready = await new Promise((res, rej) => { let o = ''; const t = setTimeout(() => rej(new Error('startup')), 120000)
  child.stdout.on('data', c => { o += c; const l = o.split('\n').find(v => v.startsWith('{"base":')); if (l) { clearTimeout(t); res(JSON.parse(l)) } }) })
const STATES = ['next', 'waiting', 'brief', 'trimmed', 'review']
let turn = 0
function sse() {
  turn++
  const f = [['session_init', { session_id: 'lc02-shots' }]]
  STATES.forEach((s, i) => {
    const id = `t${turn}-${i}`, tool = views[s].next_step?.tool ?? 'get_task'
    f.push(['tool_request', { tool_use_id: id, tool_name: tool, args: {}, safety_tier: 'read' }])
    f.push(['tool_result', { tool_use_id: id, tool_name: tool, result: { ok: true, _task: views[s] } }])
  })
  f.push(['token', { delta: 'Here are the plans.' }], ['turn_done', { usage_acc: { input_tokens: 1, output_tokens: 1 } }])
  return f.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
}
const browser = await chromium.launch()
const report = {}
try {
  const cred = JSON.parse(await fs.readFile(ready.credentials_file, 'utf8'))
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  const errors = []; page.on('pageerror', e => errors.push(String(e)))
  await page.goto(ready.base)
  await page.locator('#email').fill(cred.email); await page.locator('#password').fill(cred.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL(/\/projects\?signed_in=/)
  const csrf = (await context.cookies()).find(c => c.name === 'pypsa_gui_csrf').value
  const headers = { 'X-CSRF-Token': csrf }
  assert((await context.request.post(ready.base + '/api/io/import/netcdf', { headers, multipart: { file: { name: 'oj-grid.nc', mimeType: 'application/x-netcdf', buffer: await fs.readFile(ready.network_file) } } })).ok())
  assert((await context.request.post(ready.base + '/api/projects/oj-grid', { headers })).ok())
  await context.route(u => u.hostname !== '127.0.0.1', r => r.abort())
  await context.route('**/api/chat/stream', r => r.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse() }))
  await page.addInitScript(() => { localStorage.setItem('network-diagram:current-project', 'oj-grid'); if (!localStorage.getItem('lc02-companion')) localStorage.setItem('network-diagram:assistant-dock', 'open') })
  await page.goto(ready.base + '/app')
  await page.getByTestId('chat-input').waitFor()
  for (const mode of ['expert', 'guided']) {
    await page.getByTestId(`ui-mode-${mode}`).click()
    const before = await page.getByTestId('assistant-task-card').count()
    await page.getByTestId('chat-input').fill('Show my plans.'); await page.getByTestId('chat-send').click()
    await page.getByTestId('assistant-task-card').nth(before + 4).waitFor()
    for (const [i, s] of STATES.entries()) {
      const card = page.getByTestId('assistant-task-card').nth(before + i)
      await card.scrollIntoViewIfNeeded()
      await card.locator('xpath=..').screenshot({ path: path.join(out, `${mode}-${s}-close.png`) })
      report[`${mode}-${s}`] = await card.innerText()
      if (s === 'next' || s === 'review') await page.screenshot({ path: path.join(out, `${mode}-${s}-dock.png`) })
    }
  }
  // Companion: dock closed; the plan card lives in the dock transcript, the companion shows its status line.
  await page.evaluate(() => { localStorage.setItem('lc02-companion', '1'); localStorage.setItem('network-diagram:assistant-dock', 'closed') })
  await page.reload()
  const input = page.getByTestId('companion-live-input')
  await page.getByTestId('companion-launcher').waitFor(); await page.waitForTimeout(1500)
  if (!await input.isVisible()) { await page.getByTestId('companion-live').focus(); await page.keyboard.press('Enter') }
  await page.waitForTimeout(800); await page.screenshot({ path: path.join(out, 'debug-after.png') }); report.live = await page.getByTestId('companion-launcher').getAttribute('data-live')
  await input.fill('Show my plans.', { timeout: 5000 }); await page.getByTestId('companion-live-send').click()
  await page.waitForTimeout(2500)
  await page.screenshot({ path: path.join(out, 'guided-companion.png') })
  report.companion = await page.getByTestId('companion-launcher').innerText().catch(() => '')
  report.page_errors = errors
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); child.kill('SIGTERM')
}
