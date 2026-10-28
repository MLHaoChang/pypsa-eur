// D12 renderer probe: frame time of N extruded 10 x 20 x 8 m buildings in the
// repo's three.js (pypsa-gui/frontend/node_modules/three), headless Chromium.
// NOT a packaged device: software GL in a cloud container. It sizes the
// budget; real-device confirmation stays pending (decision-register D12).
//
//   cd pypsa-gui/frontend && node ../../.scratch/site-scene-generation/sc-00-n/evidence/render_probe.mjs
import { createRequire } from 'node:module'
const { chromium } = createRequire(import.meta.url)('playwright')  // the preinstalled one
import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const threePath = resolve('node_modules/three/build/three.module.js')
const threeCore = resolve('node_modules/three/build/three.core.js')
const page = `<!doctype html><html><body style="margin:0"><canvas id="c" width="1280" height="720"></canvas>
<script type="module">
import * as THREE from '/three.module.js'
window.measure = async (n, merged) => {
  const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('c'), antialias: true })
  renderer.setSize(1280, 720, false)
  const scene = new THREE.Scene()
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.2))
  const camera = new THREE.PerspectiveCamera(50, 1280 / 720, 1, 5000)
  camera.position.set(600, 700, 900); camera.lookAt(600, 0, 400)
  const shape = new THREE.Shape([[-5,-10],[5,-10],[5,10],[-5,10]].map(([x, y]) => new THREE.Vector2(x, y)))
  const base = new THREE.ExtrudeGeometry(shape, { depth: 8, bevelEnabled: false }).rotateX(-Math.PI / 2)
  const mat = new THREE.MeshStandardMaterial({ color: 0xb0b8c0 })
  if (merged) {
    const mesh = new THREE.InstancedMesh(base, mat, n)
    const m = new THREE.Matrix4()
    for (let i = 0; i < n; i++) { m.makeTranslation((i % 50) * 25, 0, Math.floor(i / 50) * 25); mesh.setMatrixAt(i, m) }
    scene.add(mesh)
  } else {
    for (let i = 0; i < n; i++) { const mesh = new THREE.Mesh(base, mat); mesh.position.set((i % 50) * 25, 0, Math.floor(i / 50) * 25); scene.add(mesh) }
  }
  for (let i = 0; i < 10; i++) renderer.render(scene, camera)
  const times = []
  for (let i = 0; i < 60; i++) {
    camera.position.x = 600 + 50 * Math.sin(i / 10)
    const t = performance.now(); renderer.render(scene, camera); renderer.getContext().finish(); times.push(performance.now() - t)
  }
  times.sort((a, b) => a - b)
  const out = { n, mode: merged ? 'instanced' : 'mesh-per-building', median_ms: +times[30].toFixed(2), p95_ms: +times[57].toFixed(2),
                draw_calls: renderer.info.render.calls, triangles: renderer.info.render.triangles,
                heap_mb: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null,
                gl: renderer.getContext().getParameter(renderer.getContext().RENDERER) }
  renderer.dispose()
  return out
}
window.ready = true
</script></body></html>`

const server = createServer((req, res) => {
  if (req.url === '/three.module.js') { res.setHeader('content-type', 'text/javascript'); return res.end(readFileSync(threePath)) }
  if (req.url === '/three.core.js') { res.setHeader('content-type', 'text/javascript'); return res.end(readFileSync(threeCore)) }
  res.setHeader('content-type', 'text/html'); res.end(page)
}).listen(0)
const url = `http://127.0.0.1:${server.address().port}/`
const browser = await chromium.launch({ args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--enable-precise-memory-info'] })
const results = []
for (const [n, merged] of [[1, false], [100, false], [1000, false], [2000, false], [2000, true]]) {
  const p = await browser.newPage({ viewport: { width: 1280, height: 720 } })
  await p.goto(url); await p.waitForFunction('window.ready === true')
  results.push(await p.evaluate(([n, merged]) => window.measure(n, merged), [n, merged]))
  await p.close()
}
console.log(JSON.stringify({ chromium: browser.version(), three: '0.186.1', viewport: '1280x720', results }, null, 2))
await browser.close(); server.close()
