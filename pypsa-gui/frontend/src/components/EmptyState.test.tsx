import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import EmptyState from './EmptyState'

describe('EmptyState', () => {
  it('is a status region with a title, the reason as visible text, and one action', () => {
    const onClick = vi.fn()
    render(<EmptyState testId="e" title="Not run yet" action={{ label: 'Run it ›', onClick }}>The study has not finished.</EmptyState>)
    const box = screen.getByRole('status')
    expect(box.getAttribute('data-testid')).toBe('e')
    expect(box.textContent).toContain('Not run yet')
    expect(box.textContent).toContain('The study has not finished.')
    expect(box.getAttribute('title')).toBeNull()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Run it ›' }))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('shows no button without an action', () => {
    render(<EmptyState title="No project open">Open a project.</EmptyState>)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
