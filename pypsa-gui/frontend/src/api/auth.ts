import client from './client'

export interface AuthUser {
  id: string
  email: string
  status: string
  is_super_admin: boolean
  org_id: string | null
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
  client.post<OkResponse>('/auth/forgot-password', payload).then(r => r.data)

export const authApi = {
  login: (payload: LoginPayload) =>
    client.post<LoginResponse>('/auth/login', payload).then(r => r.data),
  logout: () =>
    client.post<OkResponse>('/auth/logout', undefined, quietAuthRequest).then(r => r.data),
  me: () =>
    client.get<AuthUser>('/auth/me', quietAuthRequest).then(r => r.data),
  forgotPassword,
  forgot: forgotPassword,
  setPassword: (payload: PasswordTokenPayload) =>
    client.post<OkResponse>('/auth/set-password', payload).then(r => r.data),
  resetPassword: (payload: PasswordTokenPayload) =>
    client.post<OkResponse>('/auth/reset-password', payload).then(r => r.data),
}
