// The data-centre create form (DC 22): prefilled defaults, the labels shared
// with the Backup and Power architecture sections, visible architecture hints,
// the create body, and a refusal that opens with the field label.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ARCH_FIELD, ARCH_LABEL } from '../../DcArchitectureSection'
import { BACKUP_MODE_FIELD, MODE_LABEL } from '../../DcBackupSection'
import { dcFieldOf, dcRefusalText, DC_FIELD_LABEL, ARCH_HINT, BACKUP_HINT } from '../dcLoadForm'
import { DcLoadForm } from './DcLoadForm'

afterEach(cleanup)

function mount(templateId = 'eh_datacenter') {
  const create = { mutate: vi.fn(), isPending: false }
  render(<DcLoadForm templateId={templateId} templateName="Data Center Energy Hub" create={create}
    disabledReason={null} onCancel={() => {}} />)
  return create
}

describe('DcLoadForm', () => {
  it('shares its labels with the Backup and Power architecture sections', () => {
    mount()
    expect(DC_FIELD_LABEL.backup).toBe(BACKUP_MODE_FIELD)
    expect(DC_FIELD_LABEL.architecture).toBe(ARCH_FIELD)
    const arch = screen.getByLabelText(ARCH_FIELD) as HTMLSelectElement
    expect([...arch.options].map(o => o.text)).toEqual(Object.values(ARCH_LABEL))
    const backup = screen.getByLabelText(BACKUP_MODE_FIELD) as HTMLSelectElement
    expect([...backup.options].map(o => o.text)).toEqual(Object.values(MODE_LABEL))
    expect(Object.keys(ARCH_HINT)).toEqual(Object.keys(ARCH_LABEL))
    expect(Object.keys(BACKUP_HINT)).toEqual(Object.keys(MODE_LABEL))
  })

  it('shows every architecture hint as visible text', () => {
    mount()
    const hints = screen.getByTestId('dc-form-arch-hints')
    for (const [k, label] of Object.entries(ARCH_LABEL)) {
      expect(within(hints).getByTestId(`dc-form-arch-hint-${k}`).textContent).toContain(`${label}: ${ARCH_HINT[k as keyof typeof ARCH_HINT]}`)
    }
  })

  it('creates unedited with the example defaults', async () => {
    const create = mount()
    await userEvent.click(screen.getByTestId('dc-form-create'))
    expect(create.mutate).toHaveBeenCalledTimes(1)
    const arg = create.mutate.mock.calls[0][0]
    expect(arg.templateId).toBe('eh_datacenter')
    expect(arg.params).toEqual({ dc_load: {
      pue_at_design: 1.3, architecture: 'ac', backup: { mode: 'genset' },
      phases: [{ cod: '2030-01-01', it_mw: 36 }],
    } })
  })

  it('sends the chosen architecture, backup mode, PUE and IT load', async () => {
    const create = mount()
    await userEvent.selectOptions(screen.getByLabelText(ARCH_FIELD), 'sst')
    await userEvent.selectOptions(screen.getByLabelText(BACKUP_MODE_FIELD), 'hybrid')
    await userEvent.clear(screen.getByTestId('dc-form-pue')); await userEvent.type(screen.getByTestId('dc-form-pue'), '1.4')
    await userEvent.clear(screen.getByTestId('dc-form-it')); await userEvent.type(screen.getByTestId('dc-form-it'), '20')
    await userEvent.click(screen.getByTestId('dc-form-create'))
    const dc = create.mutate.mock.calls[0][0].params.dc_load
    expect(dc).toMatchObject({ architecture: 'sst', backup: { mode: 'hybrid' }, pue_at_design: 1.4,
      phases: [{ it_mw: 20 }] })
  })

  it('staged: no typed IT load, the plan keeps its phases', async () => {
    const create = mount('eh_datacenter_staged')
    expect(screen.queryByTestId('dc-form-it')).toBeNull()
    expect(screen.getByTestId('dc-form-phases').textContent).toContain('12 MW from 2027')
    await userEvent.click(screen.getByTestId('dc-form-create'))
    expect(create.mutate.mock.calls[0][0].params.dc_load.phases).toBeUndefined()
  })

  it('checks beside the field and disables Create', async () => {
    const create = mount()
    await userEvent.clear(screen.getByTestId('dc-form-pue')); await userEvent.type(screen.getByTestId('dc-form-pue'), '0.9')
    expect(screen.getByTestId('dc-form-error-pue').textContent).toMatch(/^PUE at design: /)
    expect((screen.getByTestId('dc-form-create') as HTMLButtonElement).disabled).toBe(true)
    expect(create.mutate).not.toHaveBeenCalled()
  })

  it('shows a server refusal beside its field, opening with the label', async () => {
    const create = mount()
    create.mutate.mockImplementation((_a, opts) => opts?.onError?.({ response: { status: 422, data: { detail: {
      code: 'dc_load_invalid', field: 'dc_load.pue_at_design',
      message: 'dc_load.pue_at_design: a PUE of 1.05 cannot include the architecture’s own conversion losses' } } } }))
    await userEvent.click(screen.getByTestId('dc-form-create'))
    const e = screen.getByTestId('dc-form-error-pue').textContent ?? ''
    expect(e.startsWith('PUE at design: a PUE of 1.05')).toBe(true)
    expect(e).not.toContain('dc_load')
  })

  it('maps backend field keys to form fields', () => {
    expect(dcFieldOf('dc_load.phases.0.it_mw')).toBe('it_load')
    expect(dcFieldOf('dc_load.architecture')).toBe('architecture')
    expect(dcFieldOf('dc_load.backup.mode')).toBe('backup')
    expect(dcFieldOf('dc_load')).toBeNull()
    expect(dcRefusalText(null, 'dc_load: must be an object')).toBe('must be an object')
  })
})
