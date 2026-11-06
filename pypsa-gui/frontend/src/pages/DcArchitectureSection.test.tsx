// DC 05: the power-architecture section shows only on a data-centre project;
// Compare runs in place and shows every architecture with the summary line,
// the price used and the footnote in both modes; a changed spec marks the
// result out of date; Expert adds the price field, the extra columns and the
// loss-chain expander.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { DcArchitectureCompare } from '../api/campusElectrical'
import fixture from '../test/fixtures/dcArchitectureCompare.json'
import DcArchitectureSection, { SERVICE_CHIP } from './DcArchitectureSection'

const campus = vi.hoisted(() => ({ compareDcArchitecture: vi.fn() }))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: campus }
})
const results = vi.hoisted(() => ({ getEhTemplate: vi.fn() }))
vi.mock('../api/simulation', () => ({ resultsApi: results }))
const ui = vi.hoisted(() => ({ uiMode: 'expert' as 'expert' | 'guided' }))
vi.mock('../store/uiStore', () => ({ useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui) }))

const data = fixture as unknown as DcArchitectureCompare
const text = (el: HTMLElement) => (el.textContent ?? '').replace(/ /g, ' ')

function renderIt(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return { client, ...render(<QueryClientProvider client={client}><DcArchitectureSection name="DC A" /></QueryClientProvider>) }
}

describe('DcArchitectureSection', () => {
  beforeEach(() => { vi.resetAllMocks(); ui.uiMode = 'expert' })
  afterEach(cleanup)

  it('shows nothing on a project without a dc_load', async () => {
    results.getEhTemplate.mockResolvedValue({ id: 'eh_ipp_hybrid' })
    renderIt()
    await waitFor(() => expect(results.getEhTemplate).toHaveBeenCalled())
    expect(screen.queryByTestId('dc-architecture')).toBeNull()
  })

  it('compares in place in Expert with the price, chips, expander and footnote', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: { architecture: 'mv_ups' } } })
    campus.compareDcArchitecture.mockResolvedValue(data)
    renderIt()
    expect(text(await screen.findByTestId('dc-architecture-own'))).toBe('MV UPS (set when the project is created)')
    await user.type(screen.getByTestId('dc-architecture-price'), '90')
    await user.click(screen.getByTestId('dc-architecture-compare'))
    expect(campus.compareDcArchitecture).toHaveBeenCalledWith('DC A', 90)
    expect(text(await screen.findByTestId('dc-architecture-summary')))
      .toBe(`${data.summary.cheapest_text} · ${data.summary.most_services_text}`)
    for (const v of data.variants) expect(screen.getByTestId(`dc-architecture-row-${v.architecture}`)).toBeTruthy()
    expect(screen.getByText('Efficiency to rack')).toBeTruthy()
    expect(screen.getByText(`NPV vs this project (M ${data.currency}, ${data.money_year})`)).toBeTruthy()
    expect(screen.getByText('Peak within import limit')).toBeTruthy()
    expect(screen.getAllByTitle(SERVICE_CHIP.voltage_control.full).length).toBeGreaterThan(0)
    expect(text(screen.getByTestId('dc-architecture-footnote'))).toBe('A screening estimate, with no solve. Outage proof is in Backup.')
    expect(text(screen.getByTestId('dc-architecture-source'))).toBe(`Source: defaults pack ${data.pack_version}.`)
    expect(text(screen.getByTestId('dc-architecture-price-used'))).toContain('assumed')
    expect(screen.getAllByText(data.rms_caveat + '.')).toHaveLength(1)
    await user.click(screen.getByLabelText('Loss chain of Solid-state transformer'))
    expect(text(screen.getByTestId('dc-architecture-chain-sst'))).toContain('beyond the PUE boundary')
    expect(screen.getByTestId('dc-architecture').getAttribute('data-complete')).toBe('true')
  })

  it('Guided shows the same summary and price used, without the Expert columns', async () => {
    ui.uiMode = 'guided'
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: {} } })
    campus.compareDcArchitecture.mockResolvedValue(data)
    renderIt()
    expect(text(await screen.findByTestId('dc-architecture-own'))).toBe('AC (LV UPS) (set when the project is created), assumed')
    expect(screen.queryByTestId('dc-architecture-price')).toBeNull()
    await user.click(screen.getByTestId('dc-architecture-compare'))
    expect(campus.compareDcArchitecture).toHaveBeenCalledWith('DC A', null)
    expect(text(await screen.findByTestId('dc-architecture-summary'))).toContain('Most grid services')
    expect(text(screen.getByTestId('dc-architecture-price-used'))).toContain('assumed')
    expect(screen.queryByText('Efficiency to rack')).toBeNull()
    expect(screen.queryByLabelText('Loss chain of Solid-state transformer')).toBeNull()
  })

  it('Guided asks for a price in place when the project has no tariff energy rate', async () => {
    ui.uiMode = 'guided'
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: {} } })
    campus.compareDcArchitecture.mockRejectedValueOnce({ response: { status: 422, data: { detail: {
      error_kind: 'dc_architecture_no_energy_price',
      message: 'energy_price_per_mwh: the project has no readable tariff energy component; give a price per MWh',
    } } } })
    campus.compareDcArchitecture.mockResolvedValueOnce(data)
    renderIt()
    await user.click(await screen.findByTestId('dc-architecture-compare'))
    expect(text(await screen.findByTestId('dc-architecture-error'))).toContain('give a price per MWh')
    await user.type(screen.getByTestId('dc-architecture-price'), '80')
    await user.click(screen.getByTestId('dc-architecture-compare'))
    expect(campus.compareDcArchitecture).toHaveBeenLastCalledWith('DC A', 80)
    expect(text(await screen.findByTestId('dc-architecture-price-used'))).toMatch(/\(request\) assumed/)
  })

  it('Guided keeps the price field hidden after any other refusal', async () => {
    ui.uiMode = 'guided'
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: {} } })
    campus.compareDcArchitecture.mockRejectedValue({ response: { status: 422, data: { detail: {
      error_kind: 'dc_architecture_pack_row', message: 'the defaults pack lacks a row',
    } } } })
    renderIt()
    await user.click(await screen.findByTestId('dc-architecture-compare'))
    await screen.findByTestId('dc-architecture-error')
    expect(screen.queryByTestId('dc-architecture-price')).toBeNull()
  })

  it('reads out of date when the stored spec changes after a compare', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: { backup: { mode: 'genset' } } } })
    campus.compareDcArchitecture.mockResolvedValue(data)
    const { client } = renderIt()
    await user.click(await screen.findByTestId('dc-architecture-compare'))
    await screen.findByTestId('dc-architecture-result')
    expect(screen.queryByTestId('dc-architecture-stale')).toBeNull()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: { backup: { mode: 'hybrid' } } } })
    await client.refetchQueries()
    expect(await screen.findByTestId('dc-architecture-stale')).toBeTruthy()
    expect(screen.getByTestId('dc-architecture').getAttribute('data-complete')).toBe('false')
  })

  it('shows a refusal in place', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue({ dc_load: { spec: {} } })
    campus.compareDcArchitecture.mockRejectedValue({ response: { data: { detail: {
      error_kind: 'dc_architecture_no_energy_price', message: 'energy_price_per_mwh: give a price per MWh' } } } })
    renderIt()
    await user.click(await screen.findByTestId('dc-architecture-compare'))
    expect(await screen.findByTestId('dc-architecture-error')).toBeTruthy()
  })
})
