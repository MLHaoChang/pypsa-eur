import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { authApi } from '../api/auth'
import { useAuth } from '../auth/AuthProvider'

const MIN_PASSWORD_LENGTH = 8

// Plain words for the stored role value; an unknown value shows as stored.
export const ROLE_LABELS: Record<string, string> = { admin: 'Admin', member: 'Member' }
export const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role

function errorText(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 400 && typeof error.response.data?.detail === 'string') return error.response.data.detail
    if (error.response?.status === 429) return 'Too many failed attempts. Try again later.'
  }
  return 'Could not change the password. Try again.'
}

const inputClass = 'w-full px-2 py-1.5 rounded border border-border bg-bg text-[12px] text-text'

// Small account page (AO03-PAGE-1): email (read only), workspaces with the
// user's role, and change password. A display name needs a user column, which
// does not exist yet, so it is not offered.
export default function AccountPage() {
  const { user } = useAuth()
  const account = useQuery({ queryKey: ['auth', 'account'], queryFn: authApi.account })
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setDone(false)
    if (next.length < MIN_PASSWORD_LENGTH) return setError(`The new password needs at least ${MIN_PASSWORD_LENGTH} characters.`)
    if (next !== confirm) return setError('The new passwords do not match.')
    setBusy(true)
    setError(null)
    try {
      await authApi.changePassword({ current_password: current, new_password: next })
      setCurrent(''); setNext(''); setConfirm('')
      setDone(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const email = account.data?.email ?? user?.email ?? ''

  return (
    <main className="max-w-xl mx-auto p-6 space-y-6 text-text" data-testid="account-page">
      <div className="flex items-baseline justify-between">
        <h1 className="text-[15px] font-semibold">Account</h1>
        <Link to="/projects" className="text-[11px] text-accent underline">Back to projects</Link>
      </div>

      <section className="space-y-1">
        <label htmlFor="account-email" className="text-[10px] font-mono uppercase tracking-[0.14em] text-muted">Email</label>
        <input id="account-email" className={inputClass} value={email} readOnly aria-describedby="account-email-note" />
        <p id="account-email-note" className="text-[10px] text-muted">Your email is your sign-in name. It cannot be changed here. Ask a workspace admin.</p>
      </section>

      <section className="space-y-1" aria-label="Workspaces">
        <h2 className="text-[10px] font-mono uppercase tracking-[0.14em] text-muted">Workspaces</h2>
        {account.isError ? (
          <p className="text-[12px] text-danger" role="alert">Could not load your workspaces.</p>
        ) : account.data && account.data.workspaces.length === 0 ? (
          <p className="text-[12px] text-muted">You are not a member of a workspace yet.</p>
        ) : (
          <ul className="border border-border rounded divide-y divide-border">
            {(account.data?.workspaces ?? []).map(w => (
              <li key={w.org_id} className="flex justify-between px-3 py-1.5 text-[12px]">
                <span>{w.name}</span>
                <span className="text-muted">{roleLabel(w.role)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form onSubmit={submit} className="space-y-3" aria-label="Change password">
        <h2 className="text-[10px] font-mono uppercase tracking-[0.14em] text-muted">Change password</h2>
        <div className="space-y-1">
          <label htmlFor="account-current" className="text-[11px]">Current password</label>
          <input id="account-current" type="password" autoComplete="current-password" required className={inputClass}
            value={current} onChange={e => { setCurrent(e.target.value); setDone(false) }} />
        </div>
        <div className="space-y-1">
          <label htmlFor="account-new" className="text-[11px]">New password</label>
          <input id="account-new" type="password" autoComplete="new-password" required minLength={MIN_PASSWORD_LENGTH} className={inputClass}
            value={next} onChange={e => { setNext(e.target.value); setDone(false) }} />
          <p className="text-[10px] text-muted">At least {MIN_PASSWORD_LENGTH} characters. Your other sessions are signed out.</p>
        </div>
        <div className="space-y-1">
          <label htmlFor="account-confirm" className="text-[11px]">Confirm new password</label>
          <input id="account-confirm" type="password" autoComplete="new-password" required className={inputClass}
            value={confirm} onChange={e => { setConfirm(e.target.value); setDone(false) }} />
        </div>
        {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
        {done && <p className="text-[12px] text-accent" role="status">Password changed. Your other sessions were signed out.</p>}
        <button type="submit" disabled={busy}
          className="px-3 py-1.5 rounded bg-accent text-white text-[12px] font-medium disabled:opacity-60">
          {busy ? 'Changing…' : 'Change password'}
        </button>
      </form>
    </main>
  )
}
