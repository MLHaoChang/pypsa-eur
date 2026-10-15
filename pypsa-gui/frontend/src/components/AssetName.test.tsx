import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import AssetName from './AssetName'
import { useUIStore } from '../store/uiStore'

afterEach(() => { cleanup(); useUIStore.setState({ uiMode: 'guided' }) })

describe('AssetName', () => {
  it('shows the display name, with no raw id, in Guided', () => {
    useUIStore.setState({ uiMode: 'guided' })
    render(<AssetName id="grid_import" />)
    const el = screen.getByText('Grid import')
    expect(el.getAttribute('title')).toBeNull()
    expect(document.body.textContent).not.toContain('grid_import')
  })

  it('keeps the raw id on hover in Expert', () => {
    useUIStore.setState({ uiMode: 'expert' })
    render(<AssetName id="grid_import" />)
    expect(screen.getByText('Grid import').getAttribute('title')).toBe('grid_import')
  })

  it('adds no tooltip when the name is already the id', () => {
    useUIStore.setState({ uiMode: 'expert' })
    render(<AssetName id="Bus 0" />)
    expect(screen.getByText('Bus 0').getAttribute('title')).toBeNull()
  })
})
