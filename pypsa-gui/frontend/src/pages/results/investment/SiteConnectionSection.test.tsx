// The Site connection section (IC U1 follow-up b), in its own file so Expert →
// Economics can mount it next to the Investment tab (Economics in Expert, E0):
// the form while the commercial root is missing or a result needs it, else a
// summary line that opens the form.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../../../api/network'
import SiteConnectionSection from './SiteConnectionSection'

vi.mock('../../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getLinks: vi.fn().mockResolvedValue([]),
    getBuses: vi.fn().mockResolvedValue([]), getGenerators: vi.fn().mockResolvedValue([]) } }
})

const ROOT = { poc_link: 'import', export_link: 'export', timezone: 'Europe/Berlin' }

function renderSection(props: Parameters<typeof SiteConnectionSection>[0]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><SiteConnectionSection {...props} /></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.mocked(networkApi.getLinks).mockClear() })

describe('SiteConnectionSection', () => {
  it('shows the form while the project has no commercial root', () => {
    renderSection({ config: null, needed: false })
    expect(screen.getByRole('heading', { name: 'Site connection' })).toBeTruthy()
    expect(screen.queryByTestId('ic-site-connection-summary')).toBeNull()
  })

  it('shows the form when a result needs the root, even with one set', () => {
    renderSection({ config: ROOT as never, needed: true })
    expect(screen.getByRole('heading', { name: 'Site connection' })).toBeTruthy()
  })

  it('summarises a set root and opens the form to edit it', () => {
    renderSection({ config: ROOT as never, needed: false })
    const summary = screen.getByTestId('ic-site-connection-summary')
    expect(summary.textContent).toMatch(/imports through import, exports through export, site time Europe\/Berlin/)
    const edit = screen.getByRole('button', { name: 'Edit site connection' })
    expect(edit.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(edit)
    expect(edit.getAttribute('aria-expanded')).toBe('true')
  })
})
