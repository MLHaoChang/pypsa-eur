import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CompanionLauncher from './CompanionLauncher'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

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
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBeNull()
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

  it('grows on hover and reveals the actions from a larger character', () => {
    mockMotion(false)
    render(
      <CompanionLauncher
        profileLabel="Model"
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    const root = screen.getByTestId('companion-launcher')
    const svg = screen.getByTestId('companion-character').querySelector('svg')
    expect(svg?.getAttribute('width')).toBe('112')
    expect(svg?.getAttribute('height')).toBe('112')
    expect(root.getAttribute('data-hovered')).toBe('false')
    expect(screen.getByTestId('companion-actions').className.split(/\s+/)).toContain('companion-actions')
    fireEvent.mouseEnter(root)
    expect(root.getAttribute('data-hovered')).toBe('true')
    fireEvent.mouseLeave(root)
    expect(root.getAttribute('data-hovered')).toBe('false')
  })

  it('shows response work separately from unavailable audio', () => {
    mockMotion(false)
    render(
      <CompanionLauncher
        workState="running"
        profileLabel="Model"
        liveOpen
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('thinking')
    expect(screen.getByTestId('companion-launcher').getAttribute('data-media-state')).toBe('off')
    expect(screen.getByTestId('companion-work-status').textContent).toBe('Response in progress')
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('companion-speak').getAttribute('aria-pressed')).toBeNull()
  })

  it('shows the character while closed and a grid node while Live is open', () => {
    mockMotion(false)
    const props = {
      profileLabel: 'Model',
      onCompose: vi.fn(),
      onSpeak: vi.fn(),
      onLive: vi.fn(),
      onProfiles: vi.fn(),
    }
    const { rerender } = render(<CompanionLauncher {...props} />)
    expect(screen.getByTestId('companion-character').getAttribute('data-shape')).toBe('character')
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('false')
    rerender(<CompanionLauncher {...props} workState="running" liveOpen />)
    expect(screen.getByTestId('companion-character').getAttribute('data-shape')).toBe('live-node')
    expect(screen.getByTestId('companion-live-node')).toBeTruthy()
    expect(screen.getByTestId('companion-live').getAttribute('aria-pressed')).toBe('true')
  })

  it('flashes the user line and drops it when the echo clears, with no transcript card', () => {
    mockMotion(false)
    const props = {
      profileLabel: 'Model',
      liveOpen: true,
      onCompose: vi.fn(),
      onSpeak: vi.fn(),
      onLive: vi.fn(),
      onProfiles: vi.fn(),
    }
    const { rerender } = render(
      <CompanionLauncher {...props} workState="running" liveEcho="List the projects" />,
    )
    expect(screen.getByTestId('companion-live-echo').textContent).toBe('List the projects')
    expect(screen.queryByTestId('companion-outcome')).toBeNull()
    rerender(<CompanionLauncher {...props} workState="idle" liveEcho="" />)
    expect(screen.queryByTestId('companion-live-echo')).toBeNull()
  })

  it('omits an empty caption instead of rendering a blank line', () => {
    mockMotion(false)
    render(
      <CompanionLauncher
        profileLabel="Model"
        liveOpen
        liveCaption=""
        onCompose={vi.fn()}
        onSpeak={vi.fn()}
        onLive={vi.fn()}
        onProfiles={vi.fn()}
      />,
    )
    expect(screen.queryByTestId('companion-live-caption')).toBeNull()
    expect(screen.getByTestId('companion-live-input')).toBeTruthy()
  })

  it('keeps every decorative animation and transition behind prefers-reduced-motion: no-preference', () => {
    const indexCss = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const start = indexCss.indexOf('.companion-actions {')
    const end = indexCss.indexOf('@keyframes companion-react')
    const block = indexCss.slice(start, end)
    const gate = block.indexOf('@media (prefers-reduced-motion: no-preference)')
    expect(gate).toBeGreaterThan(-1)
    const outside = block.slice(0, gate)
    expect(outside).not.toMatch(/animation\s*:/)
    expect(outside).not.toMatch(/transition\s*:/)
    // Actions are discoverable without hover on every pointer/motion path.
    expect(outside).toMatch(/\.companion-actions\s*\{[^}]*opacity:\s*1/)
    const gated = block.slice(gate)
    expect(gated).not.toMatch(/opacity:\s*0\s*;/)
    expect(gated).toMatch(/\.companion-idle\s*\{[^}]*animation:/)
    expect(gated).toMatch(/transition:/)
    expect(gated).toMatch(/\.companion-live-echo\s*\{[^}]*animation:/)
    expect(gated).toMatch(/\.companion-node-sector\s*\{[^}]*animation:/)
  })

  it('keeps the voice note in the DOM at idle, describing the launcher, and reveals it on focus (L2)', () => {
    mockMotion(false)
    render(<CompanionLauncher profileLabel="Claude" onCompose={vi.fn()} onSpeak={vi.fn()} onLive={vi.fn()}
      onProfiles={vi.fn()} />)
    const root = screen.getByTestId('companion-launcher')
    const described = document.getElementById(root.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toMatch(/Continuous voice is not connected/)
    expect(screen.getByRole('button', { name: 'Start voice' }).hasAttribute('disabled')).toBe(true)
    // Idle: the extras are clipped by CSS (still in the DOM), never removed.
    expect(screen.getByTestId('companion-voice-note').classList.contains('companion-extras')).toBe(true)
    expect(screen.getByTestId('companion-extras').contains(screen.getByTestId('companion-profile'))).toBe(true)
    expect(root.getAttribute('data-hovered')).toBe('false')
    fireEvent.focus(screen.getByTestId('companion-speak'))
    expect(root.getAttribute('data-hovered')).toBe('true')
  })

  it('clips the extras only while idle, never with display:none (L2)', () => {
    const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
    const rule = css.slice(css.indexOf(".companion-launcher:not([data-hovered='true'])"))
    expect(rule).toMatch(/:not\(\[data-live='open'\]\):not\(:focus-within\) \.companion-extras \{/)
    expect(rule.slice(0, rule.indexOf('}'))).toMatch(/clip: rect\(0 0 0 0\)/)
    expect(rule.slice(0, rule.indexOf('}'))).not.toMatch(/display:\s*none/)
  })
})

describe('CompanionLauncher continuous speech (LC-07)', () => {
  const base = { profileLabel: 'Claude', onCompose: vi.fn(), onSpeak: vi.fn(), onLive: vi.fn(), onProfiles: vi.fn() }
  const speech = (state: 'off' | 'starting' | 'listening', available = true) =>
    ({ available, state, start: vi.fn(), stop: vi.fn() })

  it('shows no Speak control when speech is unavailable, and media stays off', () => {
    mockMotion(false)
    render(<CompanionLauncher {...base} liveOpen speech={speech('off', false)} />)
    expect(screen.queryByTestId('companion-live-speak')).toBeNull()
    expect(screen.getByTestId('companion-launcher').getAttribute('data-media-state')).toBe('off')
    expect(screen.getByText(/Continuous voice is not connected/)).toBeTruthy()
  })

  it('when speech is available the voice note points to Speak instead of a disabled Start voice', () => {
    mockMotion(false)
    render(<CompanionLauncher {...base} liveOpen speech={speech('off')} />)
    expect(screen.queryByRole('button', { name: 'Start voice' })).toBeNull()
    expect(screen.getByTestId('companion-voice-note').textContent).toMatch(/press Speak beside Send/)
  })

  it('Speak starts; while listening the toggle reads Stop and media is live', async () => {
    mockMotion(false)
    const off = speech('off')
    const { rerender } = render(<CompanionLauncher {...base} liveOpen speech={off} />)
    await userEvent.click(screen.getByTestId('companion-live-speak'))
    expect(off.start).toHaveBeenCalledTimes(1)
    const on = speech('listening')
    rerender(<CompanionLauncher {...base} liveOpen speech={on} />)
    const toggle = screen.getByTestId('companion-live-speak')
    expect(toggle.textContent).toBe('Stop')
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('companion-launcher').getAttribute('data-media-state')).toBe('live')
    await userEvent.click(toggle)
    expect(on.stop).toHaveBeenCalledTimes(1)
  })

  it('after speech stops, the typed composer still sends from the same Live composer', async () => {
    mockMotion(false)
    const onLiveSubmit = vi.fn()
    render(<CompanionLauncher {...base} liveOpen speech={speech('off')} onLiveSubmit={onLiveSubmit}
      liveCaption="Speech stopped because this tab was hidden. Type here to continue in the same conversation." />)
    expect(screen.getByTestId('companion-live-caption').textContent).toMatch(/Type here to continue/)
    await userEvent.type(screen.getByTestId('companion-live-input'), 'size the battery{Enter}')
    expect(onLiveSubmit).toHaveBeenCalledWith('size the battery')
  })
})
