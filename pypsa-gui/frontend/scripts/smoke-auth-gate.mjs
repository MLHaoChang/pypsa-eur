#!/usr/bin/env node
/**
 * Live smoke: hit the running Vite dev server and assert anonymous document
 * routes cannot boot the React workbench.
 *
 * Usage: node scripts/smoke-auth-gate.mjs [baseUrl]
 */
const base = (process.argv[2] || 'http://127.0.0.1:5173').replace(/\/$/, '')

function fail(msg) {
  console.error('FAIL:', msg)
  process.exit(1)
}

function ok(msg) {
  console.log('OK:', msg)
}

async function get(path, headers = {}) {
  const res = await fetch(`${base}${path}`, {
    headers: {
      Accept: 'text/html,application/xhtml+xml,*/*',
      'User-Agent': 'Mozilla/5.0 pypsa-auth-gate-smoke',
      ...headers,
    },
    redirect: 'manual',
  })
  const text = await res.text()
  return { res, text }
}

function assertLoginDocument(label, text) {
  if (!text.includes('data-pypsa-page="login"')) fail(`${label}: missing login page marker`)
  if (!text.includes('Sign in')) fail(`${label}: missing Sign in`)
  if (text.includes('src/main.tsx') || text.includes('/src/main.tsx')) {
    fail(`${label}: leaked React entry main.tsx`)
  }
  if (text.includes('h-screen') && text.includes('AppErrorBoundary')) {
    fail(`${label}: looks like workbench shell`)
  }
  ok(label)
}

const { res: rootRes, text: rootText } = await get('/')
if (rootRes.status !== 200) fail(`GET / expected 200, got ${rootRes.status}`)
assertLoginDocument('GET /', rootText)

const { res: appRes, text: appText } = await get('/app')
if (appRes.status !== 200) fail(`GET /app expected 200, got ${appRes.status}`)
assertLoginDocument('GET /app', appText)

const { res: projectsRes, text: projectsText } = await get('/projects')
if (projectsRes.status !== 200) fail(`GET /projects expected 200, got ${projectsRes.status}`)
assertLoginDocument('GET /projects', projectsText)

const { res: spaRes } = await get('/spa.html')
if (spaRes.status !== 302 && spaRes.status !== 301) {
  fail(`GET /spa.html anonymous expected redirect, got ${spaRes.status}`)
}
const loc = spaRes.headers.get('location') || ''
if (!loc.endsWith('/') && loc !== '/') fail(`GET /spa.html redirect location=${loc}`)
ok('GET /spa.html redirects anonymous users')

const { text: backendDownHelp } = await get('/')
if (!backendDownHelp.includes('Backend API is not running')) {
  fail('login page missing backend-down guidance')
}
ok('login page explains a stopped backend')

// Login and confirm authenticated documents can reach the SPA entry.
const loginRes = await fetch(`${base}/api/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    email: 'admin@example.com',
    password: 'admin-pass-123',
  }),
})
if (!loginRes.ok) fail(`login API failed: ${loginRes.status} ${await loginRes.text()}`)
const setCookie = loginRes.headers.getSetCookie?.() || []
const cookie = setCookie.map((c) => c.split(';')[0]).join('; ')
  || (loginRes.headers.get('set-cookie') || '').split(';')[0]
if (!cookie) fail('login API did not return Set-Cookie')

const { res: authedProjects, text: authedText } = await get('/projects', { Cookie: cookie })
if (authedProjects.status !== 200) fail(`authed GET /projects expected 200, got ${authedProjects.status}`)
if (!authedText.includes('src/main.tsx') && !authedText.includes('/src/main.tsx')) {
  fail('authed GET /projects should serve spa.html with React entry')
}
if (authedText.includes('data-pypsa-page="login"')) fail('authed GET /projects should not be the login page')
ok('authed GET /projects serves React SPA')

console.log('All auth-gate smoke checks passed against', base)
