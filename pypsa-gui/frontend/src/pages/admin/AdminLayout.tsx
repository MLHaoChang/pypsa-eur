import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '../../auth/AuthProvider'
import { getAdminDefaultPath } from './helpers'
import EmailSettingsPage from './EmailSettingsPage'
import LegacyMigratePage from './LegacyMigratePage'
import OrgsPage from './OrgsPage'
import UsersPage from './UsersPage'

const NAV_ITEMS = [
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/organizations', label: 'Organizations' },
  { to: '/admin/legacy-migrate', label: 'Legacy migrate' },
  { to: '/admin/email-settings', label: 'Email settings' },
]

export default function AdminLayout() {
  const { user } = useAuth()
  const defaultPath = getAdminDefaultPath(user)

  return (
    // The admin console is part of the always-dark workspace surface, like
    // the projects home — see index.css for what data-pypsa-surface pins.
    <div
      className="min-h-screen bg-[var(--brand-black)] text-[var(--brand-ink)] [color-scheme:dark]"
      data-pypsa-surface="brand-dark"
    >
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-6 py-6 lg:flex-row lg:px-8 lg:py-8">
        <aside className="flex w-full flex-col justify-between gap-8 rounded-[32px] bg-gradient-to-br from-[#14090a] via-[#4a1418] to-[#8c1d22] p-8 text-white shadow-[0_24px_80px_rgba(0,0,0,0.55)] lg:max-w-[320px]">
          <div className="space-y-6">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[var(--brand-ink)]">PyPSA Studio</p>
              <h1 className="text-4xl font-semibold tracking-[-0.03em]">Admin console</h1>
              <p className="text-sm leading-6 text-[var(--brand-ink-dim)]">
                Manage organizations, onboard users, migrate legacy projects, and verify outbound email delivery.
              </p>
            </div>
            <div className="rounded-3xl border border-white/12 bg-white/8 p-5 backdrop-blur-sm">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--brand-ink)]">Signed in as</p>
              <p className="mt-2 break-all text-sm font-medium text-white">{user?.email ?? 'unknown user'}</p>
              <p className="mt-2 text-xs text-[var(--brand-ink-dim)]">
                {user?.is_super_admin ? 'Super-admin' : 'Organization admin'}
              </p>
            </div>
            <nav className="space-y-2">
              {NAV_ITEMS.map(item => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => (
                    `flex items-center justify-between rounded-2xl px-4 py-3 text-sm font-medium transition ${
                      isActive
                        ? 'bg-[var(--brand-red)] text-[var(--brand-on-red)]'
                        : 'border border-white/12 text-white hover:bg-white/8'
                    }`
                  )}
                >
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="space-y-3">
            <Link
              className="inline-flex w-full items-center justify-center rounded-2xl bg-[var(--brand-red)] px-4 py-3 text-sm font-medium text-[var(--brand-on-red)] transition hover:brightness-110"
              to="/projects"
            >
              Back to projects
            </Link>
          </div>
        </aside>

        <main className="flex min-h-[70vh] flex-1 overflow-hidden rounded-[32px] border border-[var(--brand-line)] bg-[var(--brand-surface)] shadow-[0_20px_60px_rgba(0,0,0,0.45)]">
          <Routes>
            <Route element={<Navigate replace to={defaultPath} />} index />
            <Route element={<UsersPage />} path="users" />
            <Route element={<OrgsPage />} path="organizations" />
            <Route element={<LegacyMigratePage />} path="legacy-migrate" />
            <Route element={<EmailSettingsPage />} path="email-settings" />
            <Route element={<Navigate replace to={defaultPath} />} path="*" />
          </Routes>
        </main>
      </div>
    </div>
  )
}
