import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CompanionLauncher from './CompanionLauncher'

function mockMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? matches : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

describe('CompanionLauncher', () => {
  afterEach(() => cleanup())

  it('offers compose and speak without claiming the microphone is open', async () => {
    mockMotion(false)
    const onCompose = vi.fn()
    const onSpeak = vi.fn()
    const onLive = vi.fn()
    const onProfiles = vi.fn()
    const user = userEvent.setup()
    render(
      <CompanionLauncher
        profileLabel="Claude"
        onCompose={onCompose}
        onSpeak={onSpeak}
        onLive={onLive}
        onProfiles={onProfiles}
      />,
    )
    const root = screen.getByTestId('companion-launcher')
    expect(root.getAttribute('data-presence')).toBe('idle')
    expect(root.getAttribute('data-motion')).toBe('on')
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBe('false')
    await user.click(screen.getByTestId('companion-compose'))
    await user.click(screen.getByTestId('companion-speak'))
    await user.click(screen.getByTestId('companion-live'))
    await user.click(screen.getByTestId('companion-profile'))
    expect(onCompose).toHaveBeenCalledOnce()
    expect(onSpeak).toHaveBeenCalledOnce()
    expect(onLive).toHaveBeenCalledOnce()
    expect(onProfiles).toHaveBeenCalledOnce()
    expect(screen.getByTestId('companion-profile').textContent).toBe('Claude')
  })

  it('drops decorative motion when the user prefers reduced motion', () => {
    mockMotion(true)
    render(
      <CompanionLauncher
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-launcher').getAttribute('data-motion')).toBe('off')
    expect(screen.getByTestId('companion-character').className.split(/\s+/)).not.toContain('companion-idle')
  })

  it('marks a listening presence only when that presence is passed in', () => {
    mockMotion(false)
    render(
      <CompanionLauncher
        presence="listening"
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('listening')
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBe('false')
  })
})
