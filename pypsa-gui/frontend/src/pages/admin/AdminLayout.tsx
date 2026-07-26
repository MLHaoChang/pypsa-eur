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
    <div className="min-h-screen bg-[#f3f7f2] text-slate-950">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-6 py-6 lg:flex-row lg:px-8 lg:py-8">
        <aside className="flex w-full flex-col justify-between gap-8 rounded-[32px] bg-gradient-to-br from-[#173426] via-[#244b36] to-[#3f6d4f] p-8 text-white shadow-[0_24px_80px_rgba(23,52,38,0.22)] lg:max-w-[320px]">
          <div className="space-y-6">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#dff6e7]">PyPSA GUI</p>
              <h1 className="text-4xl font-semibold tracking-[-0.03em]">Admin console</h1>
              <p className="text-sm leading-6 text-[#d6ecd9]">
                Manage organizations, onboard users, migrate legacy projects, and verify outbound email delivery.
              </p>
            </div>
            <div className="rounded-3xl border border-white/12 bg-white/8 p-5 backdrop-blur-sm">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#dff6e7]">Signed in as</p>
              <p className="mt-2 break-all text-sm font-medium text-white">{user?.email ?? 'unknown user'}</p>
              <p className="mt-2 text-xs text-[#d6ecd9]">
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
                        ? 'bg-white text-[#173426]'
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
              className="inline-flex w-full items-center justify-center rounded-2xl bg-white px-4 py-3 text-sm font-medium text-[#173426] transition hover:bg-[#eef7f0]"
              to="/projects"
            >
              Back to projects
            </Link>
          </div>
        </aside>

        <main className="flex min-h-[70vh] flex-1 overflow-hidden rounded-[32px] border border-[#d6e1d8] bg-white shadow-[0_20px_60px_rgba(23,52,38,0.10)]">
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
