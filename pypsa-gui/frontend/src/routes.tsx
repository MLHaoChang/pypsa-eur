import { Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import { authEnabled } from './auth/config'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import AdminLayout from './pages/admin/AdminLayout'
import LoginPage from './pages/auth/LoginPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import SetPasswordPage from './pages/auth/SetPasswordPage'
import ProjectsHomePage from './pages/ProjectsHomePage'

export default function AppRoutes() {
  if (!authEnabled) {
    return (
      <Routes>
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
