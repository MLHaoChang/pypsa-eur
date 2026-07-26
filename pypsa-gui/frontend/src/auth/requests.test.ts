import { afterEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '../api/auth'
import {
  FORGOT_PASSWORD_SUCCESS_MESSAGE,
  MISSING_PASSWORD_TOKEN_MESSAGE,
  PASSWORDS_MUST_MATCH_MESSAGE,
  loginWithPassword,
  requestPasswordReset,
  submitPasswordToken,
} from './requests'

describe('loginWithPassword', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('trims the email address before calling authApi.login', async () => {
    const loginSpy = vi.spyOn(authApi, 'login').mockResolvedValue({
      ok: true,
      user: {
        id: 'user-1',
        email: 'person@example.com',
        status: 'active',
        is_super_admin: false,
        org_id: 'org-1',
        role: 'member',
      },
    })

    const response = await loginWithPassword('  person@example.com  ', 'hunter2')

    expect(loginSpy).toHaveBeenCalledWith({
      email: 'person@example.com',
      password: 'hunter2',
    })
    expect(response.user.email).toBe('person@example.com')
  })
})

describe('requestPasswordReset', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses the dedicated forgot-password email when provided', async () => {
    const forgotSpy = vi.spyOn(authApi, 'forgotPassword').mockResolvedValue({ ok: true })

    const result = await requestPasswordReset(' reset@example.com ', 'login@example.com')

    expect(forgotSpy).toHaveBeenCalledWith({ email: 'reset@example.com' })
    expect(result).toEqual({ ok: true, message: FORGOT_PASSWORD_SUCCESS_MESSAGE })
  })

  it('returns a validation message when both email fields are blank', async () => {
    const forgotSpy = vi.spyOn(authApi, 'forgotPassword').mockResolvedValue({ ok: true })

    const result = await requestPasswordReset('   ', ' ')

    expect(forgotSpy).not.toHaveBeenCalled()
    expect(result).toEqual({ ok: false, message: 'Enter an email address first.' })
  })
})

describe('submitPasswordToken', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses the set-password endpoint in set mode', async () => {
    const setPasswordSpy = vi.spyOn(authApi, 'setPassword').mockResolvedValue({ ok: true })

    const result = await submitPasswordToken({
      mode: 'set',
      token: ' invite-token ',
      password: 'Password123',
      confirmPassword: 'Password123',
    })

    expect(setPasswordSpy).toHaveBeenCalledWith({
      token: 'invite-token',
      password: 'Password123',
    })
    expect(result).toEqual({ ok: true })
  })

  it('returns a validation message when the token is missing', async () => {
    const resetPasswordSpy = vi.spyOn(authApi, 'resetPassword').mockResolvedValue({ ok: true })

    const result = await submitPasswordToken({
      mode: 'reset',
      token: '  ',
      password: 'Password123',
      confirmPassword: 'Password123',
    })

    expect(resetPasswordSpy).not.toHaveBeenCalled()
    expect(result).toEqual({ ok: false, message: MISSING_PASSWORD_TOKEN_MESSAGE })
  })

  it('returns a validation message when the passwords do not match', async () => {
    const resetPasswordSpy = vi.spyOn(authApi, 'resetPassword').mockResolvedValue({ ok: true })

    const result = await submitPasswordToken({
      mode: 'reset',
      token: 'reset-token',
      password: 'Password123',
      confirmPassword: 'Password456',
    })

    expect(resetPasswordSpy).not.toHaveBeenCalled()
    expect(result).toEqual({ ok: false, message: PASSWORDS_MUST_MATCH_MESSAGE })
  })
})
