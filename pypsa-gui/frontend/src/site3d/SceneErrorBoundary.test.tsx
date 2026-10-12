import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SceneErrorBoundary from './SceneErrorBoundary'

function Boom(): never { throw new Error('model failed') }

describe('SceneErrorBoundary', () => {
  it('renders its fallback when a child throws, and its children otherwise', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { rerender } = render(<SceneErrorBoundary fallback={<span>parametric</span>} resetKey={1}><Boom /></SceneErrorBoundary>)
    expect(screen.getByText('parametric')).toBeTruthy()
    rerender(<SceneErrorBoundary fallback={<span>parametric</span>} resetKey={2}><span>hero</span></SceneErrorBoundary>)
    expect(screen.getByText('hero')).toBeTruthy()
  })
})
