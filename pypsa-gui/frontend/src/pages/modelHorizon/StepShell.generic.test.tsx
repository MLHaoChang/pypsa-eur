// S8 (plan "StepShell.tsx: generic step id type parameter with `labels` and a
// `navLabel` prop defaulting to "Model horizon steps""). The decision study's
// intake reuses the frame with its own step ids; the step id type and the
// labels are one parameter, so a step without a label, or a `current` that is
// not a step, is a TYPE error (the `@ts-expect-error` lines below fail
// `tsc --noEmit` if StepShell ever accepts any string again).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { StepShell, STEP_LABELS } from './StepShell'

afterEach(() => cleanup())

type Id = 'site' | 'load'
const LABELS: Record<Id, string> = { site: 'Your site', load: 'Your consumption' }

describe('StepShell with its own step ids', () => {
  it('labels the rail from `labels` and names the nav by `navLabel`', () => {
    render(
      <StepShell<Id> steps={['site', 'load']} current="load" onSelect={vi.fn()}
        labels={LABELS} navLabel="Decision study steps" title="Step 2 of 2">
        <p>body</p>
      </StepShell>,
    )
    const nav = screen.getByRole('navigation', { name: 'Decision study steps' })
    expect(nav.textContent).toContain('Your site')
    expect(nav.textContent).toContain('Your consumption')
    expect(screen.getByRole('button', { name: /Your consumption/ }).getAttribute('aria-current')).toBe('step')
  })

  it('keeps the Model Horizon nav name by default', () => {
    render(
      <StepShell steps={['mode', 'window']} current="mode" onSelect={vi.fn()}
        labels={STEP_LABELS} title="t"><p>b</p></StepShell>,
    )
    expect(screen.getByRole('navigation', { name: 'Model horizon steps' })).toBeTruthy()
  })

  it('refuses, at the type level, a step that is not one of the labelled ids', () => {
    const el = (
      // @ts-expect-error 'goal' is not an Id: the labels and the ids are one parameter
      <StepShell<Id> steps={['site']} current="goal" onSelect={vi.fn()} labels={LABELS} title="t">
        <p>b</p>
      </StepShell>
    )
    const missing = (
      // @ts-expect-error a label for 'load' is missing
      <StepShell<Id> steps={['site']} current="site" onSelect={vi.fn()} labels={{ site: 'Your site' }} title="t">
        <p>b</p>
      </StepShell>
    )
    expect(el).toBeTruthy()
    expect(missing).toBeTruthy()
  })
})
