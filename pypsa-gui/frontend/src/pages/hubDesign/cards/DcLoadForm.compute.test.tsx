// DC 05: the compute row's wording and fold arrow, single-year and staged.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DcLoadForm } from './DcLoadForm'

afterEach(cleanup)
/** jsdom does not toggle a <details> on a summary click: open it and fire the event a browser would. */
const open = () => {
  const d = screen.getByTestId('dc-form-compute') as HTMLDetailsElement
  d.open = true
  fireEvent(d, new Event('toggle'))
}
const mount = (templateId: string) => render(<DcLoadForm templateId={templateId} templateName="Data Center Energy Hub"
  create={{ mutate: vi.fn(), isPending: false }} disabledReason={null} onCancel={() => {}} />)

describe('compute row', () => {
  it('shows ▸ closed and ▾ open', async () => {
    mount('eh_datacenter')
    const summary = screen.getByText(/Derive IT from compute/)
    expect(summary.textContent).toMatch(/^▸ /)
    open()
    expect(screen.getByText(/Derive IT from compute/).textContent).toMatch(/^▾ /)
  })
  it('single-year: typed IT, "typed wins", mismatch names both figures in two sentences', async () => {
    mount('eh_datacenter')
    const user = userEvent.setup()
    open()
    await user.type(screen.getByTestId('dc-form-compute-kw'), '1.5')
    await user.type(screen.getByTestId('dc-form-compute-util'), '0.8')
    await user.type(screen.getByTestId('dc-form-compute-count-0'), '20000')
    expect(screen.getByTestId('dc-form-compute-derived-0').textContent).toBe('derived 24.0 MW IT · typed 36 MW IT (typed wins)')
    expect(screen.getByTestId('dc-form-compute-mismatch-0').textContent)
      .toBe('Compute gives 24.0 MW. The typed IT load is 36.0 MW and is the one used.')
  })
  it('staged: the plan holds the IT load, so "planned" and "plan wins"', async () => {
    mount('eh_datacenter_staged')
    const user = userEvent.setup()
    open()
    expect(screen.getByText(/The planned IT load always wins/)).toBeTruthy()
    await user.type(screen.getByTestId('dc-form-compute-kw'), '1.5')
    await user.type(screen.getByTestId('dc-form-compute-util'), '0.8')
    await user.type(screen.getByTestId('dc-form-compute-count-0'), '5000')
    expect(screen.getByTestId('dc-form-compute-derived-0').textContent).toBe('derived 6.0 MW IT · planned 12 MW IT (plan wins)')
    expect(screen.getByTestId('dc-form-compute-mismatch-0').textContent).toMatch(/The planned IT load is 12\.0 MW/)
    expect(screen.queryByText(/typed wins|typed IT/)).toBeNull()
  })
})
