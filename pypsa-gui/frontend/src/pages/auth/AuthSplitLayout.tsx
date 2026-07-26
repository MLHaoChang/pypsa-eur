import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

export function AuthSplitLayout({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <div className="min-h-screen bg-[#f3f7f2] text-slate-950 md:grid md:grid-cols-[minmax(320px,1fr)_minmax(480px,1.05fr)]">
      <section className="relative overflow-hidden bg-gradient-to-br from-[#173426] via-[#244b36] to-[#3f6d4f] px-8 py-12 text-white md:px-12 md:py-16">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(196,246,214,0.18),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(255,255,255,0.08),_transparent_28%)]" />
        <div className="relative flex h-full flex-col justify-between gap-16">
          <div className="space-y-4">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#dff6e7]">PyPSA GUI</p>
            <div className="space-y-3">
              <h1 className="max-w-md text-4xl font-semibold tracking-[-0.03em] md:text-5xl">
                Model, solve, and compare energy networks with your team.
              </h1>
              <p className="max-w-lg text-sm leading-6 text-[#d6ecd9] md:text-base">
                Secure project access, shared workspaces, and a clean path back into the studies you were
                running before you signed out.
              </p>
            </div>
          </div>
          <div className="grid gap-3 text-sm text-[#e2f2e6] sm:grid-cols-3">
            <div className="rounded-2xl border border-white/12 bg-white/6 p-4 backdrop-blur-sm">
              <div className="font-medium text-white">Shared projects</div>
              <p className="mt-1 text-xs leading-5 text-[#d6ecd9]">Work across organizations without losing ownership context.</p>
            </div>
            <div className="rounded-2xl border border-white/12 bg-white/6 p-4 backdrop-blur-sm">
              <div className="font-medium text-white">Secure recovery</div>
              <p className="mt-1 text-xs leading-5 text-[#d6ecd9]">Reset or set your password directly from the email token flow.</p>
            </div>
            <div className="rounded-2xl border border-white/12 bg-white/6 p-4 backdrop-blur-sm">
              <div className="font-medium text-white">Fast resume</div>
              <p className="mt-1 text-xs leading-5 text-[#d6ecd9]">Return to the last project you touched after sign-in.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="flex items-center justify-center px-6 py-10 md:px-10 md:py-14">
        <div className="w-full max-w-md rounded-3xl border border-[#d6e1d8] bg-white p-8 shadow-[0_20px_60px_rgba(23,52,38,0.10)] md:p-10">
          <div className="mb-8 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#315840]">Account access</p>
            <h2 className="text-3xl font-semibold tracking-[-0.02em] text-slate-950">{title}</h2>
            <p className="text-sm leading-6 text-slate-600">{subtitle}</p>
          </div>
          <div className="space-y-5">{children}</div>
        </div>
      </section>
    </div>
  )
}

export function AuthMessage({
  children,
  tone = 'info',
}: {
  children: ReactNode
  tone?: 'info' | 'error'
}) {
  const classes = tone === 'error'
    ? 'rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800'
    : 'rounded-2xl border border-[#d6e1d8] bg-[#f6faf5] px-4 py-3 text-sm leading-6 text-slate-700'
  return <div className={classes}>{children}</div>
}

export function AuthInput(
  props: InputHTMLAttributes<HTMLInputElement> & { label: string },
) {
  const { label, className, ...inputProps } = props
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      <input
        {...inputProps}
        className={`w-full rounded-2xl border border-[#cfd9d1] bg-white px-4 py-3 text-sm text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-[#315840] focus:ring-4 focus:ring-[#dff1e5] ${className ?? ''}`}
      />
    </label>
  )
}

export function AuthButton(
  props: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode },
) {
  const { children, className, ...buttonProps } = props
  return (
    <button
      {...buttonProps}
      className={`inline-flex w-full items-center justify-center rounded-2xl bg-[#234631] px-4 py-3 text-sm font-medium text-white transition hover:bg-[#1b3928] disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
    >
      {children}
    </button>
  )
}

export function AuthSecondaryButton(
  props: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode },
) {
  const { children, className, ...buttonProps } = props
  return (
    <button
      {...buttonProps}
      className={`inline-flex w-full items-center justify-center rounded-2xl border border-[#cfd9d1] bg-white px-4 py-3 text-sm font-medium text-slate-700 transition hover:border-[#9db4a6] hover:bg-[#f7faf7] disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
    >
      {children}
    </button>
  )
}
