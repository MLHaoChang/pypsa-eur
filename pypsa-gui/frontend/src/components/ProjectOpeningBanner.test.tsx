import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import ProjectOpeningBanner from './ProjectOpeningBanner'
import { useUIStore } from '../store/uiStore'

afterEach(() => { cleanup(); useUIStore.getState().setOpeningProject(null) })

describe('ProjectOpeningBanner', () => {
  it('renders nothing when no project is opening', () => {
    render(<ProjectOpeningBanner />)
    expect(screen.queryByTestId('project-opening')).toBeNull()
  })
  it('shows a progress bar naming the project while it opens', () => {
    useUIStore.getState().setOpeningProject('concentrator')
    render(<ProjectOpeningBanner />)
    expect(screen.getByRole('progressbar', { name: 'Opening concentrator' })).toBeTruthy()
    expect(screen.getByText(/Opening concentrator/)).toBeTruthy()
  })
})
