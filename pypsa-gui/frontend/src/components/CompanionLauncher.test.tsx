import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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

  it('offers Write, Dictate, and Live without claiming the microphone is open', async () => {
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
    expect(screen.getByTestId('companion-character').getAttribute('data-shape')).toBe('robot')
    expect(screen.getByTestId('companion-compose').textContent).toContain('Write')
    expect(screen.getByTestId('companion-speak').textContent).toContain('Dictate')
    expect(screen.getByTestId('companion-live').textContent).toContain('Live')
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('false')
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

  it('shows a robot while closed and morphs to the live node when Live is open', () => {
    mockMotion(false)
    const { rerender } = render(
      <CompanionLauncher
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    const root = screen.getByTestId('companion-launcher')
    let character = screen.getByTestId('companion-character')
    expect(character.getAttribute('data-shape')).toBe('robot')
    expect(character.querySelector('svg')?.getAttribute('width')).toBe('112')
    fireEvent.mouseEnter(root)
    expect(root.getAttribute('data-hovered')).toBe('true')
    fireEvent.mouseLeave(root)
    expect(root.getAttribute('data-hovered')).toBe('false')

    rerender(
      <CompanionLauncher
        presence="listening"
        profileLabel="Model"
        liveOpen
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    character = screen.getByTestId('companion-character')
    expect(character.getAttribute('data-shape')).toBe('live-node')
    expect(character.querySelector('svg')?.getAttribute('width')).toBe('96')
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByTestId('companion-profile')).toBeNull()
  })

  it('flashes the live echo and clears it without keeping a transcript card', () => {
    mockMotion(false)
    const { rerender } = render(
      <CompanionLauncher
        presence="thinking"
        profileLabel="Model"
        liveOpen
        liveEcho="List the projects"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-live-echo').textContent).toBe('List the projects')
    expect(screen.queryByTestId('companion-outcome')).toBeNull()
    rerender(
      <CompanionLauncher
        presence="listening"
        profileLabel="Model"
        liveOpen
        liveEcho=""
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.queryByTestId('companion-live-echo')).toBeNull()
  })
})
