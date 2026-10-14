import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Explain from './Explain'
import { useUIStore } from '../store/uiStore'

afterEach(() => {
  cleanup()
  useUIStore.setState({ uiMode: 'guided' })
})

describe('Explain', () => {
  it('renders a computed explanation as plain muted text, with no assistant tag', () => {
    render(<Explain text="Solar covers most daytime load." origin="computed" />)
    const note = screen.getByTestId('explain')
    expect(note.getAttribute('data-origin')).toBe('computed')
    expect(note.className).toMatch(/text-muted/)
    expect(note.className).not.toMatch(/border-l/)
    expect(screen.queryByText('Assistant')).toBeNull()
  })

  it('tags assistant narration and sets it apart with a left border', () => {
    render(<Explain text="The battery shifts the evening peak." origin="assistant" />)
    const note = screen.getByTestId('explain')
    expect(note.getAttribute('data-origin')).toBe('assistant')
    expect(note.className).toMatch(/border-l-2/)
    expect(screen.getByText('Assistant')).toBeTruthy()
    expect(screen.getByText('The battery shifts the evening peak.')).toBeTruthy()
  })

  it('shows the rule ids on hover in Expert only', () => {
    useUIStore.setState({ uiMode: 'expert' })
    render(<Explain text="Imports hit the cap." origin="computed" ruleIds={['grid.import_cap', 'grid.peak']} />)
    expect(screen.getByTestId('explain').getAttribute('title')).toBe('Rules: grid.import_cap, grid.peak')
  })

  it('hides the rule ids in Guided', () => {
    useUIStore.setState({ uiMode: 'guided' })
    render(<Explain text="Imports hit the cap." origin="computed" ruleIds={['grid.import_cap']} />)
    expect(screen.getByTestId('explain').getAttribute('title')).toBeNull()
  })

  it('renders nothing for empty text', () => {
    const { container } = render(<Explain text="  " origin="computed" />)
    expect(container.firstChild).toBeNull()
  })
})
