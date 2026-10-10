// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/** Real Chromium microphone/MediaRecorder smoke; API responses are mocked.
 * Run with an audible English WAV and Playwright available to Node:
 * PYPSA_GUI_DICTATION_AUDIO=/tmp/speech.wav CHROMIUM_BIN=/usr/bin/chromium \
 * node scripts/smoke-dictation.mjs
 * The backend's opt-in test_dictation_live.py covers the real paid API separately.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const fixture = process.env.PYPSA_GUI_DICTATION_AUDIO
assert(fixture, 'Set PYPSA_GUI_DICTATION_AUDIO to an audible speech WAV fixture.')
await fs.access(fixture)
const artifacts = await fs.mkdtemp(path.join(os.tmpdir(), 'dictation-browser-'))
const entryName = `.dictation-browser-${process.pid}.tmp`
const htmlPath = path.join(root, `${entryName}.html`)
const sourcePath = path.join(root, 'src', `${entryName}.tsx`)
let server
let browser
try {
  await fs.writeFile(htmlPath, `<!doctype html><html><head><title>Dictation smoke</title></head><body><div id="root"></div><script type="module" src="/src/${entryName}.tsx"></script></body></html>`)
  await fs.writeFile(sourcePath, `
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DictationControls from './components/DictationControls'
import { useSpeechToText } from './hooks/useSpeechToText'
import './index.css'
function Probe() {
  const [language, setLanguage] = useState('auto')
  const [draft, setDraft] = useState('')
  const [dockOpen, setDockOpen] = useState(true)
  const speech = useSpeechToText({onFinal: setDraft, language: language === 'auto' ? navigator.language : language})
  return <div style={{position:'relative',marginTop:500,width:600,padding:16}}>
    <DictationControls speech={speech} language={language} onLanguage={setLanguage} onInsert={setDraft} streaming={false} dockOpen={dockOpen} project="Demo" coarsePointer={false}/>
    <textarea aria-label="Chat draft" value={draft} onChange={e=>setDraft(e.target.value)} />
    <button onClick={()=>setDockOpen(false)}>Collapse dock</button>
  </div>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient()}><Probe/></QueryClientProvider>)
`)
  // Isolated test page: no production authentication config or backend is changed.
  server = await createServer({ configFile: false, root, appType: 'mpa',
    plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  const port = server.httpServer.address().port
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_BIN || undefined,
    headless: true, args: ['--no-sandbox', '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${path.resolve(fixture)}`] })
  const context = await browser.newContext({ viewport: { width: 1000, height: 850 }, permissions: ['microphone'] })
  await context.addInitScript(() => {
    window.__stoppedTracks = 0
    const stop = MediaStreamTrack.prototype.stop
    MediaStreamTrack.prototype.stop = function () { window.__stoppedTracks++; return stop.call(this) }
  })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let calls = 0
  let audioBytes = 0
  await page.route('**/api/dictation/config', route => route.fulfill({ json: {
    available: true, model: 'gpt-4o-mini-transcribe', languages: ['en', 'de', 'zh'], max_bytes: 10485760, max_recording_seconds: 120,
  } }))
  await page.route('**/api/dictation/transcribe', async route => {
    calls++
    const request = route.request()
    const body = request.postDataBuffer()
    assert(body.includes(Buffer.from('name="language"\r\n\r\nen')))
    const field = body.indexOf(Buffer.from('name="file"'))
    const start = body.indexOf(Buffer.from('\r\n\r\n'), field) + 4
    const boundary = Buffer.from('\r\n--' + request.headers()['content-type'].split('boundary=')[1])
    const end = body.indexOf(boundary, start)
    const audio = body.subarray(start, end)
    assert(audio.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])), 'Actual recording is not a WebM container')
    audioBytes = audio.length
    await fs.writeFile(path.join(artifacts, 'recording.webm'), audio)
    await route.fulfill({ json: { text: 'Create a project with 7.5 MW of load, do not use 75 MW.' } })
  })
  await page.goto(`http://127.0.0.1:${port}/${entryName}.html`)
  await page.getByRole('button', { name: 'Dictation settings', exact: true }).click()
  await page.getByLabel('Dictation language', { exact: true }).selectOption('en')
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await page.getByRole('button', { name: 'Pause / transcribe', exact: true }).waitFor()
  await page.waitForFunction(() => Number(document.querySelector('meter')?.getAttribute('value')) > 0)
  assert.equal(await page.getByLabel('Chat draft', { exact: true }).inputValue(), '')
  await page.getByRole('button', { name: 'Pause / transcribe', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="Review transcript"]')?.value.includes('7.5 MW'))
  assert.equal(calls, 1)
  await page.screenshot({ path: path.join(artifacts, 'review.png'), fullPage: true })
  await page.getByRole('button', { name: 'Insert into chat', exact: true }).click()
  assert((await page.getByLabel('Chat draft', { exact: true }).inputValue()).includes('7.5 MW'))
  await page.getByRole('button', { name: 'Dictation settings', exact: true }).click()
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await page.getByRole('button', { name: 'Pause / transcribe', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Collapse dock', exact: true }).click()
  await page.getByRole('dialog', { name: 'Dictation', exact: true }).waitFor({ state: 'hidden' })
  assert(await page.evaluate(() => window.__stoppedTracks >= 2))
  assert.equal(calls, 1, 'Dock collapse transcribed discarded audio')
  assert.deepEqual(errors, [])
  const result = { passed: true, realMediaRecorder: true, realLevelMeter: true,
    reviewBeforeInsert: true, dockCancel: true, transcriptionRequests: calls,
    provider: 'mocked; live API tested separately', audioBytes, artifacts }
  await fs.writeFile(path.join(artifacts, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
} finally {
  await browser?.close()
  await server?.close()
  await Promise.all([fs.rm(htmlPath, { force: true }), fs.rm(sourcePath, { force: true })])
}
