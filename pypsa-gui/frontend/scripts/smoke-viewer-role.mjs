#!/usr/bin/env node
// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Browser journey for the project viewer role (project-viewer-role 05).
 *
 *   cd pypsa-gui/frontend
 *   node scripts/smoke-viewer-role.mjs [--shots <dir>]
 *
 * Needs the API on :8000 (auth on) and Vite on :5173 (`pixi run gui`), the dev
 * admin from scripts/dev-credentials.mjs, and Chromium. The admin creates two
 * fresh members, an owner and a colleague; the owner makes a project, shares
 * it with the colleague as a viewer through the Share dialog, the colleague
 * sees the read-only line and cannot edit in Guided or Expert but still reads
 * the project, and after the owner makes them an editor a reload lets them edit.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
const { chromium } = createRequire(import.meta.url)('playwright')
import { devCredentials } from './dev-credentials.mjs'

const WEB = process.env.WEB || 'http://127.0.0.1:5173'
const shotsAt = process.argv.includes('--shots') ? process.argv[process.argv.indexOf('--shots') + 1] : null
if (shotsAt) fs.mkdirSync(shotsAt, { recursive: true })
const stamp = Date.now().toString(36)
const PASSWORD = `Pw-${stamp}-journey!`
const VIEW_LINE = /You can view this project\. Ask .* for edit access\./

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_BIN || undefined, headless: true, args: ['--no-sandbox'],
})
const csrfOf = async ctx => (await ctx.cookies()).find(c => c.name === 'pypsa_gui_csrf')?.value
const ctxFor = async () => browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })

async function loginApi(ctx, creds) {
  const r = await ctx.request.post(`${WEB}/api/auth/login`, { data: creds })
  assert.equal(r.status(), 200, `login ${creds.email}`)
  return csrfOf(ctx)
}

async function newMember(admin, csrf, orgId, email) {
  const r = await admin.request.post(`${WEB}/api/admin/users`, {
    headers: { 'X-CSRF-Token': csrf }, data: { email, role: 'member', org_id: orgId },
  })
  assert.equal(r.status(), 201, `create ${email}`)
  const link = (await r.json()).set_password_link
  assert.ok(link, 'the API returns a set-password link when no email is configured')
  const token = new URL(link).searchParams.get('token')
  const s = await admin.request.post(`${WEB}/api/auth/set-password`, { data: { token, password: PASSWORD } })
  assert.equal(s.status(), 200, `set password ${email}`)
}

let failures = 0
const check = (cond, msg) => { if (cond) console.log(`  ok  ${msg}`); else { failures++; console.log(`  FAIL ${msg}`) } }

try {
  const adminCtx = await ctxFor()
  const adminCsrf = await loginApi(adminCtx, devCredentials())
  const me = await (await adminCtx.request.get(`${WEB}/api/auth/me`)).json()
  const ownerEmail = `owner-${stamp}@example.com`
  const viewerEmail = `colleague-${stamp}@example.com`
  await newMember(adminCtx, adminCsrf, me.org_id, ownerEmail)
  await newMember(adminCtx, adminCsrf, me.org_id, viewerEmail)

  // Owner: a project of their own, opened in Guided.
  const owner = await ctxFor()
  const ownerCsrf = await loginApi(owner, { email: ownerEmail, password: PASSWORD })
  const name = `viewer-role-${stamp}`
  const made = await owner.request.post(
    `${WEB}/api/projects/from_template/3bus?name=${encodeURIComponent(name)}`, { headers: { 'X-CSRF-Token': ownerCsrf } })
  assert.equal(made.status(), 200, `create project: ${(await made.text()).slice(0, 200)}`)
  const op = await owner.newPage()
  await op.addInitScript(() => { if (!sessionStorage.getItem('vr')) { sessionStorage.setItem('vr', '1'); localStorage.setItem('network-diagram:ui-mode', 'guided') } })
  await op.goto(`${WEB}/app?project=${encodeURIComponent(name)}`)
  const share = op.getByRole('button', { name: 'Share' })
  await share.waitFor({ timeout: 30_000 })
  check(true, 'Guided: the owner sees Share in the project header')
  await share.click()
  const dlg = op.getByRole('dialog', { name: /^Share/ })
  await dlg.waitFor()
  await dlg.getByText(`Owner: ${ownerEmail}`).waitFor()
  check(await dlg.getByRole('button', { name: 'Save' }).isDisabled(), 'Share: Save is disabled until something changes')
  await dlg.getByLabel(viewerEmail).check()
  await dlg.getByLabel(`Role for ${viewerEmail}`).selectOption('viewer')
  const skip = op.getByRole('button', { name: 'Skip tour' })
  if (await skip.count()) await skip.click()
  await op.getByTestId('guide-tour').waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
  if (shotsAt) await op.screenshot({ path: path.join(shotsAt, 'guided-share-dialog.png') })
  await dlg.getByRole('button', { name: 'Save' }).click()
  await dlg.waitFor({ state: 'detached' })
  const members = await (await owner.request.get(`${WEB}/api/projects/${encodeURIComponent(name)}/members`)).json()
  check(members.find(m => m.email === viewerEmail)?.role === 'viewer', 'the colleague is stored as a viewer')

  // Colleague: reads, sees the reason, cannot edit in Guided or Expert.
  const guest = await ctxFor()
  const guestCsrf = await loginApi(guest, { email: viewerEmail, password: PASSWORD })
  const gp = await guest.newPage()
  await gp.addInitScript(() => { if (!sessionStorage.getItem('vr')) { sessionStorage.setItem('vr', '1'); localStorage.setItem('network-diagram:ui-mode', 'guided') } })
  await gp.goto(`${WEB}/app?project=${encodeURIComponent(name)}`)
  const banner = gp.getByRole('status').filter({ hasText: VIEW_LINE })
  await banner.waitFor({ timeout: 30_000 })
  check((await banner.count()) === 1, 'Guided: one read-only line names the owner')
  check((await banner.innerText()).includes(ownerEmail), 'the line names the owner by email')
  check((await gp.getByRole('button', { name: 'Share' }).count()) === 0, 'Guided: no Share for a viewer')
  check(await gp.getByRole('button', { name: 'Undo' }).isDisabled(), 'Guided: Undo is disabled')
  if (shotsAt) await gp.screenshot({ path: path.join(shotsAt, 'guided-viewer.png') })
  await gp.evaluate(() => localStorage.setItem('network-diagram:ui-mode', 'expert'))
  await gp.reload()
  await banner.waitFor({ timeout: 30_000 })
  check(await gp.locator('button[title^="Read-only"], button[title^="You can view"]').first().isVisible(), 'Expert: a disabled control carries the same reason')
  if (shotsAt) await gp.screenshot({ path: path.join(shotsAt, 'expert-viewer.png') })
  const read = await guest.request.get(`${WEB}/api/projects/${encodeURIComponent(name)}/flow`)
  check(read.status() === 200, 'the viewer can still read the project')
  const write = await guest.request.patch(`${WEB}/api/projects/${encodeURIComponent(name)}/settings`, {
    headers: { 'X-CSRF-Token': guestCsrf }, data: {} })
  check(write.status() === 403 && JSON.stringify(await write.json()).includes('project_view_only'), 'a write is refused as project_view_only')

  // Owner in Expert: the Workspace panel's Share button, then make the colleague an editor.
  await op.evaluate(() => localStorage.setItem('network-diagram:ui-mode', 'expert'))
  await op.reload()
  const wsItem = op.getByText('Workspace panel', { exact: true })
  if (!(await wsItem.isVisible().catch(() => false))) await op.getByRole('button', { name: 'Project menu' }).click()
  await wsItem.click()
  await op.getByRole('button', { name: 'Share' }).waitFor({ timeout: 30_000 })
  check((await op.getByRole('button', { name: 'Share' }).count()) === 1, 'Expert: one Share entry, in the Workspace panel')
  if (shotsAt) await op.screenshot({ path: path.join(shotsAt, 'expert-workspace-share.png') })
  await op.getByRole('button', { name: 'Share' }).click()
  const dlg2 = op.getByRole('dialog', { name: /^Share/ })
  await dlg2.getByLabel(`Role for ${viewerEmail}`).selectOption('editor')
  await dlg2.getByRole('button', { name: 'Save' }).click()
  await dlg2.waitFor({ state: 'detached' })
  await gp.reload()
  await gp.locator('[data-testid]').first().waitFor({ timeout: 30_000 })
  await gp.waitForTimeout(1500)
  check((await gp.getByRole('status').filter({ hasText: VIEW_LINE }).count()) === 0, 'after the role change and a reload the read-only line is gone')
} catch (e) {
  failures++
  console.log('  FAIL', e?.message ?? e)
} finally {
  await browser.close()
}
process.exit(failures ? 1 : 0)
