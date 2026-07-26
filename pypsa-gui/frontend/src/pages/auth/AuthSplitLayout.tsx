import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'

// Shared chrome for the React auth routes (set-password / reset-password, and
// /login when it is reached by client-side navigation). Deliberately mirrors
// the static sign-in document in `frontend/index.html` — same photo backdrop,
// glass card, and mint accent — so an emailed token link does not look like a
// different product than the page the user signed in from.
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
    <div className="relative min-h-dvh overflow-y-auto bg-[#07130f] text-[#eaf3ee]">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 bg-[url('/img/login-bg.jpg')] bg-cover bg-[center_55%]"
        style={{ filter: 'saturate(1.15) contrast(1.08) brightness(1.12)' }}
      />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0"
        style={{
          background: [
            'radial-gradient(1100px 720px at 8% 45%, rgba(5,15,12,0.92) 0%, rgba(5,15,12,0.62) 34%, rgba(5,15,12,0.18) 62%, transparent 100%)',
            'linear-gradient(90deg, rgba(5,15,12,0.86) 0%, rgba(5,15,12,0.28) 42%, rgba(5,15,12,0.12) 62%, rgba(5,15,12,0.55) 100%)',
            'linear-gradient(180deg, rgba(5,15,12,0.42) 0%, transparent 26%, transparent 62%, rgba(5,15,12,0.6) 100%)',
          ].join(','),
        }}
      />

      <div className="relative grid min-h-dvh gap-6 px-6 py-10 md:grid-cols-[1.15fr_0.85fr] md:px-10 md:py-14">
        <section className="flex flex-col justify-between gap-10">
          <div className="inline-flex items-center gap-3 text-[15px] font-bold tracking-[-0.01em]">
            <span
              className="grid h-[34px] w-[34px] place-items-center rounded-[11px] text-[15px] font-black text-[#04140e]"
              style={{
                background: 'linear-gradient(140deg,#35d39a,#0e8f66 70%)',
                boxShadow: '0 8px 28px rgba(53,211,154,0.35)',
              }}
            >
              P
            </span>
            <span>PyPSA Studio</span>
          </div>

          <div className="max-w-[30ch]">
            <span className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/14 bg-[#091813]/55 px-3 py-1.5 text-[0.72rem] uppercase tracking-[0.14em] text-[#9fb4a9] backdrop-blur">
              <span className="h-[7px] w-[7px] rounded-full bg-[#35d39a]" />
              Energy system planning
            </span>
            <h1 className="text-[clamp(2.1rem,4.4vw,3.6rem)] font-bold leading-[1.04] tracking-[-0.035em]">
              Plan the grid behind{' '}
              <span className="bg-gradient-to-r from-[#7ef0c0] via-[#35d39a] to-[#6fd8ff] bg-clip-text text-transparent">
                tomorrow&apos;s cities
              </span>
              .
            </h1>
            <p className="mt-4 max-w-[48ch] text-[clamp(0.96rem,1.15vw,1.08rem)] leading-relaxed text-[#9fb4a9]">
              Model megacity demand, data-center load growth, and transmission
              build-out — then compare scenarios side by side with your team.
            </p>
          </div>

          <div className="text-[0.74rem] tracking-[0.02em] text-[#9fb4a9]/75">
            Built on PyPSA · Powered by open optimisation
          </div>
        </section>

        <section className="flex items-center justify-center">
          <div
            className="w-full max-w-[440px] rounded-3xl border border-white/14 bg-[#081410]/[0.78] p-7 backdrop-blur-[26px] md:p-8"
            style={{ boxShadow: '0 30px 80px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.08)' }}
          >
            <div className="mb-7 space-y-2">
              <p className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-[#9fb4a9]">
                Account access
              </p>
              <h2 className="text-[1.6rem] font-semibold tracking-[-0.02em] text-[#eaf3ee]">{title}</h2>
              <p className="text-sm leading-6 text-[#9fb4a9]">{subtitle}</p>
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
  const classes = tone === 'error'
    ? 'rounded-2xl border border-[#ff8b8b]/40 bg-[#782020]/30 px-4 py-3 text-sm leading-6 text-[#ff8b8b]'
    : 'rounded-2xl border border-[#35d39a]/45 bg-[#185c42]/35 px-4 py-3 text-sm leading-6 text-[#8ff0c5]'
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
      <span className="text-[0.82rem] font-semibold tracking-[0.02em] text-[#9fb4a9]">{label}</span>
      <input
        {...inputProps}
        className={`w-full rounded-2xl border border-white/14 bg-[#040e0b]/60 px-4 py-3 text-sm text-[#eaf3ee] outline-none transition placeholder:text-[#9fb4a9]/60 focus:border-[#35d39a]/65 focus:bg-[#061410]/85 focus:ring-4 focus:ring-[#35d39a]/16 ${className ?? ''}`}
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
      className={`inline-flex w-full items-center justify-center rounded-2xl px-4 py-3.5 text-sm font-bold text-[#04140e] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#35d39a]/35 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
      style={{
        background: 'linear-gradient(135deg,#7ef0c0,#23c78d 55%,#12a774)',
        boxShadow: '0 14px 34px rgba(35,199,141,0.28)',
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
      className={`inline-flex w-full items-center justify-center rounded-2xl border border-white/14 bg-white/[0.04] px-4 py-3 text-sm font-medium text-[#eaf3ee] transition hover:border-white/25 hover:bg-white/[0.08] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#35d39a]/25 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ''}`}
    >
      {children}
    </button>
  )
}
