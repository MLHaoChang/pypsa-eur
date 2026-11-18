// S3 objective-bridge shot driver. Usage: node shots.mjs <match|mismatch|slack|rolling>
// Needs start-backend.sh (launcher.py) and start-frontend.sh running, and ../state.json
// (signed-in storage state for admin@example.com, written by login.mjs).
import { createRequire } from 'module'
const require = createRequire('/opt/node22/lib/node_modules/')
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const TOOLS = path.dirname(new URL(import.meta.url).pathname)
const OUT = path.resolve(TOOLS, '..')
const FLAG = path.join(TOOLS, 'INJECT10')
const kase = process.argv[2]
const NET = { match: 'bridge-match', mismatch: 'bridge-mismatch', slack: 'bridge-slack', rolling: 'bridge-rolling' }[kase]
if (!NET) throw new Error('unknown case ' + kase)
try { fs.unlinkSync(FLAG) } catch {}

const b = await chromium.launch()
const ctx = await b.newContext({
  viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: 'light',
  storageState: path.join(OUT, 'state.json'),
})
const p = await ctx.newPage()

// Ticket 20 workaround: the client bumps binding_generation on every send, and the
// server refuses a card answer whose generation differs from the clarification
// turn's. Pin every send in this conversation to the first value sent.
let firstGeneration = null
await p.route('**/api/chat/stream', async (route) => {
  const body = JSON.parse(route.request().postData())
  if (firstGeneration === null) firstGeneration = body.binding_generation
  console.log(`chat send: binding_generation ${body.binding_generation} -> ${firstGeneration}`)
  body.binding_generation = firstGeneration
  await route.continue({ postData: JSON.stringify(body) })
})

async function themed(name) {
  const isDark = () => p.evaluate(() => document.documentElement.dataset.theme === 'dark')
  for (const theme of ['light', 'dark']) {
    if ((await isDark()) !== (theme === 'dark')) {
      await p.click('button:has-text("Dark theme"), button:has-text("Light theme")')
      await p.waitForFunction((t) => document.documentElement.dataset.theme === t, theme)
    }
    await p.waitForTimeout(800)
    const file = path.join(OUT, `s3-${name}-${theme}.png`)
    await p.screenshot({ path: file })
    console.log('wrote', file)
  }
  // back to light for the next step
  await p.click('button:has-text("Light theme")')
  await p.waitForFunction(() => document.documentElement.dataset.theme === 'light')
}

async function scrollChatToBottom() {
  // Only the chat column (right of x=1000): scroll its message list to the end.
  await p.evaluate(() => {
    for (const node of document.querySelectorAll('*')) {
      const s = getComputedStyle(node)
      const r = node.getBoundingClientRect()
      if (r.left > 1000 && (s.overflowY === 'auto' || s.overflowY === 'scroll') && node.scrollHeight > node.clientHeight) {
        node.scrollTop = node.scrollHeight
      }
    }
  })
  await p.waitForTimeout(400)
}

async function toastGone() {
  await p.waitForSelector('text=Open Results', { state: 'hidden', timeout: 30000 }).catch(() => console.log('toast still shown'))
}

await p.goto('http://127.0.0.1:5173/projects')
await p.evaluate(() => { try { localStorage.removeItem('network-diagram:theme') } catch {} })
await p.waitForSelector('text=Import file', { timeout: 60000 })
await p.click('text=Import file')
await p.setInputFiles('input[accept*=pypsaproj]', path.join(OUT, 'nets', NET + '.nc'))
await p.waitForSelector('button:has-text("Expert")', { timeout: 60000 })
await p.click('button:has-text("Expert")')
await p.waitForTimeout(1500)
await p.click('[aria-label="Pin the assistant open"]')

if (kase === 'rolling') {
  await p.click('button:has-text("Solver Settings")')
  await p.click('input[name=solve_strategy][value=rolling]')
  const nums = p.locator('#solver-solve-strategy input[type=number]')
  await nums.nth(0).fill('3')
  await nums.nth(1).fill('0')
  await p.waitForTimeout(500)
  await p.click('button:has-text("Save settings")')
  await p.waitForTimeout(1500)
  await p.screenshot({ path: path.join(OUT, 'debug-rolling-settings.png') })
}

await p.click('button:has-text("Run LOPF")')
await p.waitForSelector('text=Optimal', { timeout: 120000 })
await p.waitForTimeout(2500)

await p.fill('[data-testid=chat-input]', 'What is the objective?')
await p.keyboard.press('Enter')
await p.waitForSelector('button:has-text("Objective-to-cost reconciliation")', { timeout: 60000 })
await p.waitForTimeout(1000)
await toastGone()
if (kase === 'match') await themed('card')

if (kase === 'mismatch') fs.writeFileSync(FLAG, '+10 EUR on cost_breakdown total\n')
await p.click('button:has-text("Objective-to-cost reconciliation")')
const done = kase === 'rolling'
  ? 'text=Not available: the objective covers only the last window.'
  : 'text=/The solver objective includes penalty/'
await p.waitForSelector(done, { timeout: 60000 })
await p.waitForTimeout(1500)
try { fs.unlinkSync(FLAG) } catch {}
await toastGone()
await scrollChatToBottom()
await themed({ match: 'matches', mismatch: 'mismatch', slack: 'penalty-not-separable', rolling: 'rolling-horizon' }[kase])

// Dump the answer text for the report.
const text = await p.evaluate(() => {
  const aside = document.querySelector('[data-testid=chat-input]').closest('aside') || document.body
  return aside.innerText
})
fs.writeFileSync(path.join(OUT, `chat-${kase}.txt`), text)
await b.close()
