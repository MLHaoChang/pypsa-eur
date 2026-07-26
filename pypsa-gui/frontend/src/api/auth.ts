import client from './client'

export interface AuthUser {
  id: string
  email: string
  status: string
  is_super_admin: boolean
  org_id: string | null
  role: 'admin' | 'member' | null
}

// One row of the non-admin org-member directory (GET /api/auth/org-members).
// Available to any authenticated org member so a project creator can pick the
// colleagues to assign — unlike the admin user list, it never crosses orgs.
export interface OrgMember {
  id: string
  email: string
  role: 'admin' | 'member' | null
}

export interface LoginPayload {
  email: string
  password: string
}

export interface PasswordTokenPayload {
  token: string
  password: string
}

export interface ForgotPasswordPayload {
  email: string
}

export interface LoginResponse {
  ok: true
  user: AuthUser
}

export interface OkResponse {
  ok: true
}

const quietAuthRequest = {
  skipAuthRedirect: true,
  skipErrorToast: true,
}

const forgotPassword = (payload: ForgotPasswordPayload) =>
  client.post<OkResponse>('/auth/forgot-password', payload, quietAuthRequest).then(r => r.data)

export const authApi = {
  // Login/password flows render their own inline errors — skip the global toast
  // so reviewers don't only see opaque axios "status code 500" popups.
  login: (payload: LoginPayload) =>
    client.post<LoginResponse>('/auth/login', payload, quietAuthRequest).then(r => r.data),
  logout: () =>
    client.post<OkResponse>('/auth/logout', undefined, quietAuthRequest).then(r => r.data),
  me: () =>
    client.get<AuthUser>('/auth/me', quietAuthRequest).then(r => r.data),
  orgMembers: () =>
    client.get<OrgMember[]>('/auth/org-members').then(r => r.data),
  forgotPassword,
  forgot: forgotPassword,
  setPassword: (payload: PasswordTokenPayload) =>
    client.post<OkResponse>('/auth/set-password', payload, quietAuthRequest).then(r => r.data),
  resetPassword: (payload: PasswordTokenPayload) =>
    client.post<OkResponse>('/auth/reset-password', payload, quietAuthRequest).then(r => r.data),
}
