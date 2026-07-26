import {
  authApi,
  type ForgotPasswordPayload,
  type LoginPayload,
  type LoginResponse,
  type OkResponse,
  type PasswordTokenPayload,
} from '../api/auth'

export const FORGOT_PASSWORD_SUCCESS_MESSAGE = 'If that account exists, a reset link has been sent.'
export const MISSING_PASSWORD_TOKEN_MESSAGE = 'Missing token. Open the full link from your email.'
export const PASSWORDS_MUST_MATCH_MESSAGE = 'Passwords must match.'

export type PasswordTokenMode = 'set' | 'reset'

export interface PasswordTokenSubmission {
  mode: PasswordTokenMode
  token: string
  password: string
  confirmPassword: string
}

type LoginRequest = (payload: LoginPayload) => Promise<LoginResponse>
type ForgotPasswordRequest = (payload: ForgotPasswordPayload) => Promise<OkResponse>
type PasswordTokenRequests = Pick<typeof authApi, 'setPassword' | 'resetPassword'>

export async function loginWithPassword(
  email: string,
  password: string,
  request: LoginRequest = authApi.login,
): Promise<LoginResponse> {
  return request({
    email: email.trim(),
    password,
  })
}

export function resolveForgotPasswordEmail(forgotEmail: string, loginEmail: string): string {
  return (forgotEmail || loginEmail).trim()
}

export async function requestPasswordReset(
  forgotEmail: string,
  loginEmail: string,
  request: ForgotPasswordRequest = authApi.forgotPassword,
): Promise<{ ok: true; message: string } | { ok: false; message: string }> {
  const email = resolveForgotPasswordEmail(forgotEmail, loginEmail)
  if (!email) {
    return { ok: false, message: 'Enter an email address first.' }
  }

  await request({ email })
  return { ok: true, message: FORGOT_PASSWORD_SUCCESS_MESSAGE }
}

export async function submitPasswordToken(
  submission: PasswordTokenSubmission,
  requests: PasswordTokenRequests = authApi,
): Promise<{ ok: true } | { ok: false; message: string }> {
  const token = submission.token.trim()
  if (!token) {
    return { ok: false, message: MISSING_PASSWORD_TOKEN_MESSAGE }
  }
  if (submission.password !== submission.confirmPassword) {
    return { ok: false, message: PASSWORDS_MUST_MATCH_MESSAGE }
  }

  const payload: PasswordTokenPayload = {
    token,
    password: submission.password,
  }

  if (submission.mode === 'set') {
    await requests.setPassword(payload)
  } else {
    await requests.resetPassword(payload)
  }

  return { ok: true }
}
