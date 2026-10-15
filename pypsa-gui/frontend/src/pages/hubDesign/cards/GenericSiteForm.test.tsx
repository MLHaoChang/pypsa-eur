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
      { templateId: 'eh_site', params: { site_type: 'hospital', connection_mw: 10 } },
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

  it('starts from an existing site's values, keeping what its user set', async () => {
    const params = { ...FORM.presets.hospital, connection_mw: 10 }
    const user = await mount(null, { params, entered: ['site_type', 'connection_mw'] })
    expect(fieldOf('connection_mw').value).toBe('10')
    await user.click(screen.getByTestId('site-create'))
    expect(create.mutate).toHaveBeenCalledWith(
      { templateId: 'eh_site', params: { site_type: 'hospital', connection_mw: 10 } },
      expect.anything())
  })
})
