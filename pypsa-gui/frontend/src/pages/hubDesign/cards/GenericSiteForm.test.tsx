// The generic site form on the Start card (G2): the site type as radio cards
// with the preset shown inline, every value tagged "default" until set,
// checks beside the field, Create disabled with "N fields need attention",
// a body with only what was set, and a server refusal put on its field.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { projectsApi } from '../../../api/projects'
import formJson from '../siteFormFixture.json'
import type { SiteForm, SiteParams } from '../genericSite'
import { GenericSiteForm } from './GenericSiteForm'
import { BACK_TO_RATE, FROM_RATE } from '../../../utils/loadGrowthWords'

const FORM = formJson as unknown as SiteForm
const create = { mutate: vi.fn(), isPending: false }

beforeEach(() => {
  vi.spyOn(projectsApi, 'getSiteTemplateForm').mockResolvedValue(FORM)
  create.mutate.mockReset(); create.isPending = false
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mount(disabledReason: string | null = null,
  seed: { params: SiteParams; entered: string[] } | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>
    <GenericSiteForm create={create} disabledReason={disabledReason} onCancel={() => {}} seed={seed} />
  </QueryClientProvider>)
  await screen.findByTestId('site-form')
  return userEvent.setup()
}

const fieldOf = (key: string) => screen.getByTestId(`site-field-${key}`) as HTMLInputElement

describe('GenericSiteForm', () => {
  it('shows the site type as a radio group with the preset inline, every value a default', async () => {
    const user = await mount()
    const group = screen.getByRole('radiogroup', { name: 'Site type' })
    expect(within(group).getAllByRole('radio')).toHaveLength(5)
    expect(screen.getByTestId('site-preset-summary').textContent)
      .toContain('Peak 8 → 12 → 15 MW, 20 MW connection')
    expect(fieldOf('connection_mw').value).toBe('20')
    const tags = screen.getAllByTestId('default-tag').length
    await user.click(screen.getByText('Hospital'))
    expect(fieldOf('connection_mw').value).toBe('8')
    expect(screen.getByTestId('site-preset-summary').textContent).toContain('3 diesel gensets of 2 MW')
    await user.clear(fieldOf('connection_mw'))
    await user.type(fieldOf('connection_mw'), '10')
    expect(screen.getAllByTestId('default-tag').length).toBeLessThan(tags)
    // no raw pack ids in Guided
    expect(screen.getByTestId('site-form').textContent).not.toMatch(/weak_flexible|eh_site|_mw/)
  })

  it('a peak above the grid available that year is refused beside it, and Create waits', async () => {
    const user = await mount()
    await user.clear(fieldOf('energization[1].mw'))
    await user.type(fieldOf('energization[1].mw'), '10')
    const peak = fieldOf('stages[1].peak_mw')
    expect(peak.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toBe(
      'Peak load in 2030 (12 MW) is above the 10 MW available from the grid that year. '
      + 'Raise the connection or lower the peak.')
    expect((screen.getByTestId('site-create') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('site-attention').textContent).toBe('1 field needs attention')
  })

  it('creates the site with only what the user set', async () => {
    const user = await mount()
    await user.click(screen.getByText('Hospital'))
    await user.clear(fieldOf('connection_mw'))
    await user.type(fieldOf('connection_mw'), '10')
    await user.click(screen.getByTestId('site-create'))
    expect(create.mutate).toHaveBeenCalledWith(
      { templateId: 'eh_site', params: { site_type: 'hospital', connection_mw: 10 }, afterCreate: expect.any(Function) },
      expect.anything())
  })

  it('puts a server refusal on its field', async () => {
    create.mutate.mockImplementation((_arg, opts) => opts.onError({
      response: { status: 422, data: { detail: {
        code: 'site_params_invalid', field: 'existing.pv_mw', message: 'existing.pv_mw: too big' } } },
    }))
    const user = await mount()
    await user.click(screen.getByTestId('site-create'))
    expect((await screen.findByRole('alert')).textContent).toBe('existing.pv_mw: too big')
    expect(fieldOf('existing.pv_mw').getAttribute('aria-invalid')).toBe('true')
  })

  it('says why it cannot create while a study runs', async () => {
    await mount('A study is still running')
    expect((screen.getByTestId('site-create') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('site-create').getAttribute('title')).toBe('A study is still running')
  })

  it("starts from an existing site's values, keeping what its user set", async () => {
    const params = { ...FORM.presets.hospital, connection_mw: 10 }
    const user = await mount(null, { params, entered: ['site_type', 'connection_mw'] })
    expect(fieldOf('connection_mw').value).toBe('10')
    await user.click(screen.getByTestId('site-create'))
    expect(create.mutate).toHaveBeenCalledWith(
      { templateId: 'eh_site', params: { site_type: 'hospital', connection_mw: 10 }, afterCreate: expect.any(Function) },
      expect.anything())
  })

  it('offers grid-outage ride-through, off by default, and sends it only when set', async () => {
    const user = await mount()
    const box = fieldOf('ride_through')
    expect(box.checked).toBe(false)
    await user.click(box)
    expect(fieldOf('ride_through').checked).toBe(true)
    await user.click(screen.getByTestId('site-create'))
    expect(create.mutate).toHaveBeenCalledWith(
      { templateId: 'eh_site', params: { site_type: 'industrial', ride_through: true }, afterCreate: expect.any(Function) },
      expect.anything())
  })

  it('refuses ride-through with no battery to hold the site, beside the field', async () => {
    const user = await mount()
    await user.click(screen.getByTestId('site-candidate-bess'))
    await user.click(fieldOf('ride_through'))
    expect(fieldOf('ride_through').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toMatch(/needs a battery/)
    expect((screen.getByTestId('site-create') as HTMLButtonElement).disabled).toBe(true)
  })

// homer-parity 04a2 (LOADGROWTH-FORM-1), UX 26's conditions 1-7.
describe('Grow from the first stage', () => {
  const growth = () => screen.queryByTestId('site-field-growth_pct') as HTMLInputElement | null
  const captions = () => screen.queryAllByText(FROM_RATE)

  it('uses the words Model Horizon uses for a value from the rate', () => {
    expect([FROM_RATE, BACK_TO_RATE]).toEqual(['from rate', 'Back to the rate'])
  })

  it('sits under the stage table with a visible unit, and is hidden with one stage', async () => {
    const user = await mount()
    expect(growth()!.closest('label')!.textContent).toBe('Grow from the first stage at% per year')
    expect(screen.getByText(/^Fills the later stages' peak load from the first stage\./)).toBeTruthy()
    await user.click(screen.getByTestId('site-stage-remove-2'))
    await user.click(screen.getByTestId('site-stage-remove-1'))
    expect(growth()).toBeNull()
  })

  it('fills the later peaks with a visible "from rate", a typed peak wins and ↺ gives it back', async () => {
    const user = await mount()
    expect(captions()).toHaveLength(0)                          // no rate: as before
    await user.type(growth()!, '5')
    expect([1, 2].map(i => fieldOf(`stages[${i}].peak_mw`).value)).toEqual(['9.26', '11.82'])
    expect(captions()).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /Back to the rate/ })).toBeNull()
    await user.clear(fieldOf('stages[1].peak_mw'))
    await user.type(fieldOf('stages[1].peak_mw'), '10')
    expect(captions()).toHaveLength(1)
    const reset = screen.getByRole('button', { name: `${BACK_TO_RATE}, stage 2` })
    expect(reset.getAttribute('title')).toBe(BACK_TO_RATE)
    await user.click(reset)
    expect(fieldOf('stages[1].peak_mw').value).toBe('9.26')
    expect(captions()).toHaveLength(2)
    // The first stage is the base: a normal field, never "from rate".
    expect(screen.queryByTestId('stages[0].peak_mw-from-rate')).toBeNull()
  })

  it('clearing the rate keeps the values and drops every caption and ↺ in one step', async () => {
    const user = await mount()
    await user.type(growth()!, '5')
    await user.clear(fieldOf('stages[2].peak_mw'))
    await user.type(fieldOf('stages[2].peak_mw'), '13')
    await user.clear(growth()!)
    expect([1, 2].map(i => fieldOf(`stages[${i}].peak_mw`).value)).toEqual(['9.26', '13'])
    expect(captions()).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Back to the rate/ })).toBeNull()
  })

  it('refuses -100 % beside the field in role="status", and clears it once valid', async () => {
    const user = await mount()
    await user.type(growth()!, '-100')
    expect(screen.getByRole('status').textContent).toBe(
      'Grow from the first stage: enter a number above -100 % per year.')
    expect(growth()!.getAttribute('aria-invalid')).toBe('true')
    await user.clear(growth()!)
    await user.type(growth()!, '-5')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a filled peak over the grid keeps the per-stage wording', async () => {
    const user = await mount()
    await user.type(growth()!, '20')                            // 8·1.2^8 = 34.4 > 20 MW
    expect(fieldOf('stages[2].peak_mw').getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toMatch(
      /^Peak load in 2035 \(34\.4 MW\) is above the 20 MW available from the grid that year\./)
  })

  it('sends the filled peaks and never the rate', async () => {
    const user = await mount()
    await user.type(growth()!, '5')
    await user.click(screen.getByTestId('site-create'))
    const params = create.mutate.mock.calls[0][0].params
    expect(params.stages.map((s: { peak_mw: number }) => s.peak_mw)).toEqual([8, 9.26, 11.82])
    expect(JSON.stringify(params)).not.toMatch(/growth|rate/i)
  })
})
})
