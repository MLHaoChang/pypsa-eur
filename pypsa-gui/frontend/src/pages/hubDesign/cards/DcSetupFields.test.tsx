// te 04: PUE, critical share, backup mode and architecture stay editable
// after creation. Each save sends one patch to PUT .../dc-load; a refusal is
// shown with nothing changed; an edited series asks before it is replaced.
import type React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { DcBackupArchFields, DcLoadFields } from './DcSetupFields'

const campus = vi.hoisted(() => ({ setDcLoad: vi.fn() }))
vi.mock('../../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../../api/campusElectrical')>('../../../api/campusElectrical')
  return { ...real, campusApi: campus }
})
const results = vi.hoisted(() => ({ getEhTemplate: vi.fn() }))
vi.mock('../../../api/simulation', () => ({ resultsApi: results }))
const ui = vi.hoisted(() => ({ currentProject: 'Site A' as string | null, readOnly: false }))
vi.mock('../../../store/uiStore', () => ({ useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui) }))

const SPEC = { pue_at_design: 1.3, critical_share: 0.75, architecture: 'ac', backup: { mode: 'genset' } }
const refusal = (data: unknown) => ({ response: { status: 422, data: { detail: data } } })

function renderIt(ui_: React.ReactElement) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui_}</QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  ui.currentProject = 'Site A'
  ui.readOnly = false
  results.getEhTemplate.mockResolvedValue({ dc_load: { spec: SPEC } })
  campus.setDcLoad.mockResolvedValue({ spec: SPEC, series_changed: true })
})
afterEach(() => cleanup())

describe('DcLoadFields', () => {
  it('shows the stored PUE and critical share and saves only what changed', async () => {
    renderIt(<DcLoadFields />)
    const pue = await screen.findByLabelText('PUE at design')
    expect((pue as HTMLInputElement).value).toBe('1.3')
    expect((screen.getByLabelText('Critical share (%)') as HTMLInputElement).value).toBe('75')
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true)
    await userEvent.clear(pue)
    await userEvent.type(pue, '1.5')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(campus.setDcLoad).toHaveBeenCalledWith('Site A', { pue_at_design: 1.5 }))
  })

  it('refuses a PUE below 1 before sending', async () => {
    renderIt(<DcLoadFields />)
    const pue = await screen.findByLabelText('PUE at design')
    await userEvent.clear(pue)
    await userEvent.type(pue, '0.9')
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByTestId('dc-load-invalid').textContent).toContain('PUE at design: enter 1 or more.')
    expect(campus.setDcLoad).not.toHaveBeenCalled()
  })

  it('shows the server refusal and leaves the values as typed', async () => {
    campus.setDcLoad.mockRejectedValue(refusal({ error_kind: 'dc_load_invalid', message: 'dc_load.critical_share: the shares exceed 1', field: 'dc_load.critical_share' }))
    renderIt(<DcLoadFields />)
    const share = await screen.findByLabelText('Critical share (%)')
    await userEvent.clear(share)
    await userEvent.type(share, '90')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect((await screen.findByTestId('dc-load-refusal')).textContent).toBe('Critical share (%): with the other shares, it adds up to more than 100 %. Lower it.')
    expect((share as HTMLInputElement).value).toBe('90')
  })

  it('asks before replacing an edited series, then resends with overwrite_series', async () => {
    campus.setDcLoad.mockRejectedValueOnce(refusal({ error_kind: 'dc_series_edited', message: 'edited' }))
    renderIt(<DcLoadFields />)
    const pue = await screen.findByLabelText('PUE at design')
    await userEvent.clear(pue)
    await userEvent.type(pue, '1.5')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByTestId('dc-series-edited')
    await userEvent.click(screen.getByRole('button', { name: 'Replace the edited profile' }))
    await waitFor(() => expect(campus.setDcLoad).toHaveBeenLastCalledWith('Site A', { pue_at_design: 1.5, overwrite_series: true }))
  })

  it('is absent on a project without a data-centre set-up', async () => {
    results.getEhTemplate.mockResolvedValue({ id: 'eh_h2_hub' })
    renderIt(<DcLoadFields />)
    await waitFor(() => expect(results.getEhTemplate).toHaveBeenCalled())
    expect(screen.queryByTestId('dc-load-fields')).toBeNull()
  })
})

describe('DcBackupArchFields', () => {
  it('stores a backup mode and an architecture as single patches', async () => {
    renderIt(<DcBackupArchFields />)
    await screen.findByTestId('dc-backup-arch-fields')
    await userEvent.click(screen.getByRole('button', { name: 'Hybrid' }))
    await waitFor(() => expect(campus.setDcLoad).toHaveBeenLastCalledWith('Site A', { backup: { mode: 'hybrid' } }))
    await userEvent.selectOptions(screen.getByLabelText('Power architecture'), 'dc_800v')
    await waitFor(() => expect(campus.setDcLoad).toHaveBeenLastCalledWith('Site A', { architecture: 'dc_800v' }))
  })

  it('shows only the architecture in the Expert architecture section', async () => {
    renderIt(<DcBackupArchFields only="architecture" />)
    await screen.findByLabelText('Power architecture')
    expect(screen.queryByTestId('dc-backup-hint')).toBeNull()
  })
})

describe('DC set-up fields: states UX asked for', () => {
  it('flags an out-of-range critical share, label first', async () => {
    renderIt(<DcLoadFields />)
    const share = await screen.findByLabelText('Critical share (%)')
    await userEvent.clear(share)
    await userEvent.type(share, '120')
    expect(share.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByTestId('dc-load-invalid').textContent).toBe('Critical share (%): enter a value above 0 and up to 100.')
  })

  it('says why Save is disabled when nothing changed', async () => {
    renderIt(<DcLoadFields />)
    await screen.findByTestId('dc-load-fields')
    expect(screen.getByTestId('dc-load-save-note').textContent).toContain('Nothing changed yet.')
  })

  it('is one read-only line with every control disabled for a read-only project', async () => {
    ui.readOnly = true
    renderIt(<><DcLoadFields /><DcBackupArchFields /></>)
    await screen.findByTestId('dc-load-fields')
    expect(screen.getAllByTestId('dc-read-only')).toHaveLength(2)
    expect((screen.getByLabelText('PUE at design') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Critical share (%)') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Power architecture') as HTMLSelectElement).disabled).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Hybrid' }))
    expect(campus.setDcLoad).not.toHaveBeenCalled()
  })
})
