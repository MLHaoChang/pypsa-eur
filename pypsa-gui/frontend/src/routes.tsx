import { Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import { useAuthMode } from './auth/AuthModeProvider'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import AdminLayout from './pages/admin/AdminLayout'
import LoginPage from './pages/auth/LoginPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import SetPasswordPage from './pages/auth/SetPasswordPage'
import ProjectsHomePage from './pages/ProjectsHomePage'

function AuthLoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-6 text-text">
      <p className="text-sm text-muted">Starting…</p>
    </div>
  )
}

export default function AppRoutes() {
  const { authEnabled, ready } = useAuthMode()

  // Always register /login so a hard redirect from the API client can land
  // somewhere real — even while we are still probing `/api/health`.
  if (!ready) {
    return (
      <Routes>
        <Route element={<LoginPage />} path="/login" />
        <Route element={<SetPasswordPage />} path="/set-password" />
        <Route element={<ResetPasswordPage />} path="/reset-password" />
        <Route element={<AuthLoadingScreen />} path="*" />
      </Routes>
    )
  }

  if (!authEnabled) {
    return (
      <Routes>
        <Route element={<LoginPage />} path="/login" />
        <Route element={<SetPasswordPage />} path="/set-password" />
        <Route element={<ResetPasswordPage />} path="/reset-password" />
        <Route element={<App />} path="*" />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route element={<LoginPage />} path="/login" />
      <Route element={<SetPasswordPage />} path="/set-password" />
      <Route element={<ResetPasswordPage />} path="/reset-password" />
      <Route element={<RequireAuth />}>
        <Route element={<Navigate replace to="/projects" />} path="/" />
        <Route element={<ProjectsHomePage />} path="/projects" />
        <Route element={<App />} path="/app" />
        <Route
          element={(
            <RequireAdmin>
              <AdminLayout />
            </RequireAdmin>
          )}
          path="/admin/*"
        />
      </Route>
      <Route element={<Navigate replace to="/projects" />} path="*" />
    </Routes>
  )
}
