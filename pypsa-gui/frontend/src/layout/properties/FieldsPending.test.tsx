import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FieldsPending from './FieldsPending'

afterEach(cleanup)

describe('FieldsPending (05d: what an edit card shows while its bus units are read)', () => {
  it('draws nothing for 200 ms, then an aria-busy status region', async () => {
    render(<FieldsPending />)
    const region = screen.getByTestId('units-pending')
    expect(region.getAttribute('role')).toBe('status')
    expect(region.getAttribute('aria-busy')).toBe('true')
    expect(region.querySelector('.invisible')).not.toBeNull()
    await waitFor(() => expect(region.querySelector('.invisible')).toBeNull(), { timeout: 1000 })
  })

  it('says a failed read keeps the cost fields closed and retries on request', async () => {
    const retry = vi.fn()
    render(<FieldsPending failed onRetry={retry} />)
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('the cost fields stay closed')
    await userEvent.click(screen.getByText('Retry'))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
