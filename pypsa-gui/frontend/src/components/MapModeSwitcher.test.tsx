import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import MapModeSwitcher from './MapModeSwitcher'
import { useUIStore } from '../store/uiStore'

describe('MapModeSwitcher', () => {
  beforeEach(() => {
    useUIStore.setState({ canvasView: 'blank', activeSlidePanel: null, paletteMode: null })
  })

  it('offers the four canvas views, the 3D site view last', () => {
    render(<MapModeSwitcher />)
    const labels = screen.getAllByRole('button').map(b => b.textContent)
    expect(labels).toEqual(['Blank', 'Satellite', 'Hybrid', 'Site 3D'])
  })

  it('clicking Site 3D switches the store to the site view and persists it', () => {
    render(<MapModeSwitcher />)
    fireEvent.click(screen.getByRole('button', { name: /Site 3D/ }))
    expect(useUIStore.getState().canvasView).toBe('site')
    expect(localStorage.getItem('network-diagram:canvas-view')).toBe('site')
  })
})
