// The dev admin sign-in for local smoke scripts. The login page ships no
// credential; the password is the single line in the generated file that
// AGENTS.md and the README's SQLite path describe. SMOKE_EMAIL /
// SMOKE_PASSWORD override both.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const DEV_ADMIN_PASSWORD_FILE = path.join(
  process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'),
  'PyPSA Studio',
  'dev-admin-password',
)

export function devCredentials() {
  const email = process.env.SMOKE_EMAIL || 'admin@example.com'
  if (process.env.SMOKE_PASSWORD) return { email, password: process.env.SMOKE_PASSWORD }
  try {
    const password = fs.readFileSync(DEV_ADMIN_PASSWORD_FILE, 'utf8').trim()
    if (password) return { email, password }
  } catch {
    // fall through to the error below
  }
  throw new Error(
    `No dev admin password: set SMOKE_PASSWORD or write it to ${DEV_ADMIN_PASSWORD_FILE}`,
  )
}
