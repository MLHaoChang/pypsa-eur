// The dialog must state the consequence, not just ask a yes/no question:
// accepting changes x, and DC OPF splits flows inversely proportional to x.
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RescaleDialog from './RescaleDialog'
import type { RescalePreview } from '../utils/rescale'

const p: RescalePreview = {
  name: 'L1',
  old_length: 1.78, new_length: 476.3,
  old: { r: 3.0, x: 17.5, b: 0.00015 },
  new: { r: 802.7, x: 4682.6, b: 0.04013 },
  rel_change: 266.6,
  skipped_reason: null,
}

describe('RescaleDialog', () => {
  it('renders nothing when there is nothing to ask about', () => {
    const { container } = render(
      <RescaleDialog previews={[]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('names the line and shows both lengths', () => {
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText('L1')).toBeDefined()
    expect(screen.getByText(/1\.78/)).toBeDefined()
    expect(screen.getByText(/476\.3/)).toBeDefined()
  })

  it('states that accepting moves solver results', () => {
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText(/results will change/i)).toBeDefined()
  })

  it('reports lines it cannot rescale instead of hiding them', () => {
    const blocked: RescalePreview = { ...p, name: 'ZERO', skipped_reason: 'old_length<=0' }
    render(<RescaleDialog previews={[p]} blocked={[blocked]} onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText('ZERO')).toBeDefined()
    expect(screen.getByText(/had no length/i)).toBeDefined()
  })

  it('reports each choice exactly once', async () => {
    const onAccept = vi.fn(); const onDecline = vi.fn()
    render(<RescaleDialog previews={[p]} blocked={[]} onAccept={onAccept} onDecline={onDecline} />)
    await userEvent.click(screen.getByRole('button', { name: /update/i }))
    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onDecline).not.toHaveBeenCalled()
  })
})
