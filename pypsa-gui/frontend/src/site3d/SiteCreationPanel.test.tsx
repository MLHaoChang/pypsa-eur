import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./SiteAnchorField', () => ({
  useSiteAnchor: () => ({ data: { version: 1, revision: 1, anchor: { lat: 47.37689, lng: 8.54169, source: 'manual', confirmed: true } } }),
}))

import { useUIStore } from '../store/uiStore'
import { useSiteCreation } from './siteCreation'
import SiteCreationPanel, { saveBlockedReason } from './SiteCreationPanel'

const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteCreationPanel /></QueryClientProvider>)
const save = () => screen.getByTestId('site-create-save') as HTMLButtonElement

beforeEach(() => {
  useUIStore.setState({ currentProject: 'p', readOnly: false, readOnlyReason: 'writable', canvasView: 'site', siteDrawRequest: false, flowReturn: null })
  useSiteCreation.getState().setOpen(true)
})

describe('New 3D site', () => {
  it('shows each limit under its field and labels the coordinate marker', () => {
    view()
    expect(screen.getAllByText('10–5,000 m')).toHaveLength(2)
    expect(screen.getAllByText('At most 1,000 m either way')).toHaveLength(2)
    expect(screen.getByTestId('site-create-marker').textContent).toBe('47.3769, 8.5417')
    expect(save().disabled).toBe(false)
  })

  it('Save is off with a visible reason: the name first, then the first bad field', () => {
    view()
    fireEvent.change(screen.getByTestId('site-create-name'), { target: { value: ' ' } })
    expect(save().disabled).toBe(true)
    expect(screen.getByTestId('site-create-save-reason').textContent).toBe('Name the site to save it')
    fireEvent.change(screen.getByTestId('site-create-name'), { target: { value: 'North campus' } })
    fireEvent.change(screen.getByTestId('site-create-width'), { target: { value: '5' } })
    expect(screen.getByTestId('site-create-save-reason').textContent).toBe('Width: 10–5,000 m')
    expect(saveBlockedReason('x', { north: 'bad' })).toBe('Offset north: at most 1,000 m either way')
  })

  it('the polygon link opens the Satellite New site tool, with Back to Site 3D', () => {
    view()
    fireEvent.click(screen.getByText('Draw the boundary on the Satellite map ›'))
    const ui = useUIStore.getState()
    expect(ui.canvasView).toBe('satellite')
    expect(ui.siteDrawRequest).toBe(true)
    expect(ui.flowReturn?.label).toBe('Site 3D')
    expect(ui.siteDrawCenter).toEqual([47.37689, 8.54169])
    expect(useSiteCreation.getState().open).toBe(false)
  })
})
