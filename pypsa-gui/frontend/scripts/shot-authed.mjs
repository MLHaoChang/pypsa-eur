#!/usr/bin/env node
/**
 * Screenshot an authenticated SPA route via Chrome DevTools Protocol.
 *
 * Usage: node scripts/shot-authed.mjs <path> <outfile> [width] [height]
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'

const [, , routePath = '/projects', outFile = '/tmp/shot.png', w = '1440', h = '900'] = process.argv
const BASE = 'http://127.0.0.1:5173'
const PORT = 9333
const PROFILE = `/tmp/cdp-profile-${Date.now()}`

const chrome = spawn('google-chrome', [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
  `--user-data-dir=${PROFILE}`, `--remote-debugging-port=${PORT}`,
  '--remote-allow-origins=*', '--hide-scrollbars',
  `--window-size=${w},${h}`, 'about:blank',
], { stdio: 'ignore' })

async function rpc(ws, id, method, params = {}) {
  ws.send(JSON.stringify({ id, method, params }))
  for (;;) {
    const raw = await new Promise((res) => ws.once('message', res))
    const msg = JSON.parse(raw.toString())
    if (msg.id === id) return msg
  }
}

try {
  let target
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      target = list.find((t) => t.type === 'page')
      if (target) break
    } catch { /* chrome still booting */ }
    await sleep(250)
  }
  if (!target) throw new Error('no CDP target')

  const { WebSocket } = await import('ws')
  const ws = new WebSocket(target.webSocketDebuggerUrl, { origin: `http://127.0.0.1:${PORT}` })
  await new Promise((res, rej) => { ws.once('open', res); ws.once('error', rej) })

  let id = 0
  await rpc(ws, ++id, 'Network.enable')
  await rpc(ws, ++id, 'Page.enable')

  // Authenticate, then set the session cookie on the CDP browser.
  const loginRes = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@example.com', password: 'admin-pass-123' }),
  })
  if (!loginRes.ok) throw new Error(`login failed ${loginRes.status}`)
  const raw = (loginRes.headers.getSetCookie?.() ?? [])[0] ?? loginRes.headers.get('set-cookie')
  const [name, value] = raw.split(';')[0].split('=')
  await rpc(ws, ++id, 'Network.setCookie', {
    name, value, domain: '127.0.0.1', path: '/', httpOnly: true,
  })

  await rpc(ws, ++id, 'Page.navigate', { url: `${BASE}${routePath}` })
  await sleep(6000)

  const shot = await rpc(ws, ++id, 'Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(outFile, Buffer.from(shot.result.data, 'base64'))
  console.log('wrote', outFile)
  ws.close()
} finally {
  chrome.kill()
}
