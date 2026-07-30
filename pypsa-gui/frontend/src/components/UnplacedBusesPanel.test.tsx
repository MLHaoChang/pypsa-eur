// What the map tells a user whose buses have no coordinates. This replaced a
// transient toast that fired once and vanished — see D5 in
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md. A message
// that disappears is most of why the original bug went unreported for so long.
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UnplacedBusesPanel from './UnplacedBusesPanel'

const noop = () => {}

describe('UnplacedBusesPanel', () => {
  it('renders nothing when every bus is placed', () => {
    const { container } = render(
      <UnplacedBusesPanel unplacedCount={0} totalCount={12} placing={false} onStartPlacing={noop} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('explains the problem in full when no bus is placed', () => {
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing={false} onStartPlacing={noop} />,
    )
    expect(screen.getByText(/no bus has a location yet/i)).toBeDefined()
    // The count and the cause both have to be on screen: "3 buses" tells the
    // user it is their whole network, "0, 0" tells them why the map is blank.
    expect(screen.getByText(/all 3 buses/i)).toBeDefined()
    expect(screen.getByText(/0, 0/)).toBeDefined()
  })

  it('shrinks to a count when only some buses are unplaced', () => {
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={12} placing={false} onStartPlacing={noop} />,
    )
    expect(screen.getByText(/3 of 12 buses unplaced/i)).toBeDefined()
    expect(screen.queryByText(/no bus has a location yet/i)).toBeNull()
  })

  it('starts placement when the action is pressed', async () => {
    const onStartPlacing = vi.fn()
    render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing={false} onStartPlacing={onStartPlacing} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /place buses on the map/i }))
    expect(onStartPlacing).toHaveBeenCalledTimes(1)
  })

  it('gets out of the way while placement is running', () => {
    // The panel sits over the map. Leaving it up during placement would cover
    // the very thing the user has to click.
    const { container } = render(
      <UnplacedBusesPanel unplacedCount={3} totalCount={3} placing onStartPlacing={noop} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
