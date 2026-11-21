import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

// Shared chrome for the React auth routes (set-password / reset-password, and
// /login when it is reached by client-side navigation). Deliberately mirrors
// the static sign-in document in `frontend/index.html` — same photo backdrop,
// glass card, and red accent — so an emailed token link does not look like a
// different product than the page the user signed in from.
//
// Colours come from `public/brand.css` via var(--brand-*), which is the same
// stylesheet index.html loads. Do NOT reintroduce literal hexes here: the pair
// would drift the moment either page is retuned.
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
    <div className="relative min-h-dvh overflow-y-auto bg-[var(--brand-black)] text-[var(--brand-ink)]">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 bg-[url('/img/login-bg.jpg')] bg-cover bg-[center_55%]"
        style={{ filter: 'var(--brand-backdrop-filter)' }}
      />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0"
        style={{ background: 'var(--brand-scrim)' }}
      />

      <div className="relative grid min-h-dvh gap-6 px-6 py-10 md:grid-cols-[1.15fr_0.85fr] md:px-10 md:py-14">
        <section className="flex flex-col justify-between gap-10">
          <div className="inline-flex items-center gap-3 text-[15px] font-bold tracking-[-0.01em]">
            <span
              className="grid h-[34px] w-[34px] place-items-center rounded-[11px] text-[15px] font-black text-[var(--brand-on-red)]"
              style={{
                background: 'linear-gradient(140deg,var(--brand-red),var(--brand-red-deeper) 70%)',
                boxShadow: 'var(--brand-mark-glow)',
              }}
            >
              P
            </span>
            <span>
              <span className="text-[var(--brand-red)]">PyPSA</span> Studio
            </span>
          </div>

          <div className="max-w-[min(620px,100%)]">
            <span className="mb-5 inline-flex items-center gap-2 rounded-full border border-[var(--brand-line)] bg-[var(--brand-chip-bg)] px-3 py-1.5 text-[0.72rem] uppercase tracking-[0.14em] text-[var(--brand-ink-dim)] backdrop-blur">
              <span className="h-[7px] w-[7px] rounded-full bg-[var(--brand-red)]" />
              Energy system planning
            </span>
            <h1 className="text-[clamp(2.1rem,4.4vw,3.6rem)] font-bold leading-[1.04] tracking-[-0.035em]">
              Advanced modelling for the{' '}
              <span className="bg-gradient-to-r from-[var(--brand-red-soft)] via-[var(--brand-red)] to-[var(--brand-red-deep)] bg-clip-text text-transparent">
                energy portfolio
              </span>
              .
            </h1>
            <p className="mt-4 max-w-[48ch] text-[clamp(0.96rem,1.15vw,1.08rem)] leading-relaxed text-[var(--brand-ink-dim)]">
              Build, solve and compare whole-system scenarios — from a single asset to
              an entire continent — on open data and an optimisation engine you can audit.
            </p>
          </div>

          <div className="text-[0.74rem] tracking-[0.02em] text-[var(--brand-ink-dim)]/75">
            Built on PyPSA · Powered by open optimisation
          </div>
        </section>

        <section className="flex items-center justify-center">
          <div
            className="w-full max-w-[440px] rounded-3xl border border-[var(--brand-line)] bg-[var(--brand-panel)] p-7 backdrop-blur-[26px] md:p-8"
            style={{ boxShadow: 'var(--brand-card-shadow)' }}
          >
            <div className="mb-7 space-y-2">
              <p className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[var(--brand-ink-dim)]">
                Account access
              </p>
              <h2 className="text-[1.6rem] font-semibold tracking-[-0.02em] text-[var(--brand-ink)]">{title}</h2>
              <p className="text-sm leading-6 text-[var(--brand-ink-dim)]">{subtitle}</p>
            </div>
            <div className="space-y-5">{children}</div>
          </div>
        </section>
      </div>
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
  // `info` is deliberately NOT red. On a red-accented page a red chip reads as
  // a failure, and this tone carries confirmations ("Password set — sign in").
  const classes = tone === 'error'
    ? 'rounded-2xl border border-[var(--brand-error-line)] bg-[var(--brand-error-bg)] px-4 py-3 text-sm leading-6 text-[var(--brand-danger)]'
    : 'rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-ok-bg)] px-4 py-3 text-sm leading-6 text-[var(--brand-ink)]'
  return (
    <div aria-live="polite" className={classes} role="status">
      {children}
    </div>
  )
}

export function AuthInput(
  props: InputHTMLAttributes<HTMLInputElement> & { label: string },
) {
  const { label, className, ...inputProps } = props
  return (
    <label className="block space-y-2">
      <span className="text-[0.82rem] font-semibold tracking-[0.02em] text-[var(--brand-ink-dim)]">{label}</span>
      <input
        {...inputProps}
        className={`w-full rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-input-bg)] px-4 py-3 text-sm text-[var(--brand-ink)] outline-none transition placeholder:text-[var(--brand-placeholder)] focus:border-[var(--brand-input-line-focus)] focus:bg-[var(--brand-input-bg-focus)] focus:ring-4 focus:ring-[var(--brand-red)]/16 ${className ?? ''}`}
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
      className={`inline-flex w-full items-center justify-center rounded-2xl px-4 py-3.5 text-sm font-bold text-[var(--brand-on-red)] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--brand-red)]/35 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
      style={{
        background: 'var(--brand-red-gradient)',
        boxShadow: 'var(--brand-red-glow)',
      }}
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
      className={`inline-flex w-full items-center justify-center rounded-2xl border border-[var(--brand-line)] bg-transparent px-4 py-3 text-sm font-medium text-[var(--brand-ink)] transition hover:border-[var(--brand-line-strong)] hover:bg-[var(--brand-ok-bg)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--brand-red)]/25 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
    >
      {children}
    </button>
  )
}
