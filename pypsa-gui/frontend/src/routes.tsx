import { Navigate, Route, Routes } from 'react-router-dom'
import App from './App'
import RequireAdmin from './auth/RequireAdmin'
import RequireAuth from './auth/RequireAuth'
import AdminLayout from './pages/admin/AdminLayout'
import LoginPage from './pages/auth/LoginPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import SetPasswordPage from './pages/auth/SetPasswordPage'
import ProjectsHomePage from './pages/ProjectsHomePage'

/**
 * Auth routes are always registered on this branch. There is no catch-all that
 * mounts the classic workbench for anonymous users — that path is what left
 * reviewers stuck on "Unnamed" + "Authentication required".
 */
export default function AppRoutes() {
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
