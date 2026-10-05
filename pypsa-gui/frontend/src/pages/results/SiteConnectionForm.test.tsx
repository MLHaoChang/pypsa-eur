// The inline "Site connection" form (IC U1 follow-up b): pick the PoC and the
// export Link from the network's Links (likely ones suggested), pick the site
// time zone, save the commercial root through commercialApi.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { commercialApi, SaveRefusedError } from '../../api/commercial'
import { networkApi } from '../../api/network'
import SiteConnectionForm from './SiteConnectionForm'
import { exportCandidates, pocCandidates, timeZoneOptions } from './siteConnection'
import { expectAllButtonsNamed } from '../../test-utils/accessibleName'
import type { Link } from '../../api/types'

vi.mock('../../api/commercial', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi, saveSiteConnection: vi.fn() } }
})
vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getLinks: vi.fn() } }
})

const link = (name: string, bus0: string, bus1: string, extra: Partial<Link> = {}) =>
  ({ name, bus0, bus1, p_min_pu: 0, eh_role: null, ...extra }) as unknown as Link

const LINKS = [
  link('poc_site', 'poc', 'site', { p_min_pu: -1 }),     // two-way: never a meter
  link('export', 'poc', 'grid'),
  link('import', 'grid', 'poc'),
  link('charger', 'site', 'ev'),
]

function renderForm(props: Partial<Parameters<typeof SiteConnectionForm>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onSaved = vi.fn()
  const utils = render(<QueryClientProvider client={qc}>
    <SiteConnectionForm initial={null} onSaved={onSaved} {...props} />
  </QueryClientProvider>)
  return { ...utils, onSaved, qc }
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(networkApi.getLinks).mockResolvedValue(LINKS)
})
afterEach(() => { cleanup(); vi.mocked(commercialApi.saveSiteConnection).mockReset() })

describe('site connection candidates', () => {
  it('suggests the Link from a grid bus as the PoC and its reverse as the export', () => {
    expect(pocCandidates(LINKS)).toEqual(['import'])
    expect(exportCandidates(LINKS, 'import')).toEqual(['export'])
  })

  it('ranks a tagged Link first and never offers a two-way Link', () => {
    const links = [...LINKS, link('feeder', 'mv', 'poc', { eh_role: 'grid_import' })]
    expect(pocCandidates(links)).toEqual(['feeder', 'import'])
    expect(pocCandidates(links)).not.toContain('poc_site')
  })

  it('offers UTC first and always the zones it is given', () => {
    const zones = timeZoneOptions('Mars/Olympus')
    expect(zones[0]).toBe('UTC')
    expect(zones).toContain('Mars/Olympus')
  })
})

describe('SiteConnectionForm', () => {
  it('preselects the suggested Links and the given time zone, and saves the root', async () => {
    vi.mocked(commercialApi.saveSiteConnection).mockResolvedValue({} as never)
    const { onSaved, container } = renderForm({ defaultTimeZone: 'Europe/Berlin' })
    const poc = await screen.findByLabelText(/point of connection/i) as HTMLSelectElement
    await waitFor(() => expect(poc.value).toBe('import'))
    expect((screen.getByLabelText(/export link/i) as HTMLSelectElement).value).toBe('export')
    expect((screen.getByLabelText(/time zone/i) as HTMLSelectElement).value).toBe('Europe/Berlin')
    // A two-way Link is not offered; the other one-way Links are.
    expect(poc.querySelector('option[value="poc_site"]')).toBeNull()
    expect(poc.querySelector('option[value="charger"]')).not.toBeNull()
    expectAllButtonsNamed(container)
    fireEvent.click(screen.getByRole('button', { name: 'Save site connection' }))
    await waitFor(() => expect(commercialApi.saveSiteConnection).toHaveBeenCalledWith(
      { poc_link: 'import', export_link: 'export', timezone: 'Europe/Berlin' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('saves "no export link" and "already site time" as nulls', async () => {
    vi.mocked(commercialApi.saveSiteConnection).mockResolvedValue({} as never)
    renderForm({ defaultTimeZone: 'UTC' })
    const exp = await screen.findByLabelText(/export link/i) as HTMLSelectElement
    await waitFor(() => expect(exp.value).toBe('export'))
    fireEvent.change(exp, { target: { value: '' } })
    fireEvent.change(screen.getByLabelText(/time zone/i), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save site connection' }))
    await waitFor(() => expect(commercialApi.saveSiteConnection).toHaveBeenCalledWith(
      { poc_link: 'import', export_link: null, timezone: null }))
  })

  it('starts from the stored root when there is one', async () => {
    renderForm({ initial: { poc_link: 'import', export_link: null, timezone: null } })
    const exp = await screen.findByLabelText(/export link/i) as HTMLSelectElement
    await waitFor(() => expect((screen.getByLabelText(/point of connection/i) as HTMLSelectElement)
      .value).toBe('import'))
    expect(exp.value).toBe('')
    expect((screen.getByLabelText(/time zone/i) as HTMLSelectElement).value).toBe('')
  })

  it('shows a refused save (422) in place and keeps the form', async () => {
    vi.mocked(commercialApi.saveSiteConnection).mockRejectedValue(
      new SaveRefusedError("commercial.export_link 'export' allows reverse flow", 'commercial_binding_invalid', 422))
    const { onSaved } = renderForm({ defaultTimeZone: 'UTC' })
    const poc = await screen.findByLabelText(/point of connection/i) as HTMLSelectElement
    await waitFor(() => expect(poc.value).toBe('import'))
    fireEvent.click(screen.getByRole('button', { name: 'Save site connection' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/allows reverse flow/)
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Save site connection' })).toBeTruthy()
  })

  it('says when the network has no Link that can be a meter', async () => {
    vi.mocked(networkApi.getLinks).mockResolvedValue([link('poc_site', 'poc', 'site', { p_min_pu: -1 })])
    renderForm()
    expect((await screen.findByTestId('site-connection-no-links')).textContent).toMatch(/one-way Link/)
    expect((screen.getByRole('button', { name: 'Save site connection' }) as HTMLButtonElement).disabled)
      .toBe(true)
  })
})
