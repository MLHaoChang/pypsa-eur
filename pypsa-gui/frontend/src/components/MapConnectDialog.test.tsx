// The map's connect dialog (M4): the Line's length defaults to the chord
// between the two buses, and confirm hands back the same parameter shapes
// the schematic's connect mode creates from.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MapConnectDialog from './MapConnectDialog'

function mount(over: Partial<React.ComponentProps<typeof MapConnectDialog>> = {}) {
  const onConfirm = vi.fn(), onClose = vi.fn()
  render(
    <MapConnectDialog
      source="A" target="B" sourceVNom={110} targetVNom={33} defaultLengthKm={2.3456789}
      anchorX={100} anchorY={100} onConfirm={onConfirm} onClose={onClose} {...over}
    />,
  )
  return { onConfirm, onClose }
}

describe('MapConnectDialog', () => {
  it('a line defaults its length to the chord, rounded to the metre', async () => {
    const { onConfirm } = mount()
    expect((screen.getByTitle(/chord/) as HTMLInputElement).value).toBe('2.346')
    await userEvent.click(screen.getByRole('button', { name: 'Add line' }))
    expect(onConfirm).toHaveBeenCalledWith({ kind: 'line', name: 'A–B', carrier: 'AC', s_nom: 100, x: 0.1, length: 2.346 })
  })

  it('falls back to 1 km when the chord is unknown', () => {
    mount({ defaultLengthKm: 0 })
    expect((screen.getByTitle(/chord/) as HTMLInputElement).value).toBe('1')
  })

  it('a transformer takes the bus voltages and refuses a mismatched step', async () => {
    const { onConfirm } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Transformer' }))
    const submit = screen.getByRole('button', { name: 'Add transformer' }) as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    await userEvent.click(submit)
    expect(onConfirm).toHaveBeenCalledWith({ kind: 'transformer', name: 'A–B', type: '', v_nom_0: 110, v_nom_1: 33, s_nom: 100, x: 0.1, tap_ratio: 1 })
    const v1 = screen.getByText('v_nom₁ (kV)').parentElement!.querySelector('input') as HTMLInputElement
    await userEvent.clear(v1)
    await userEvent.type(v1, '20')
    expect((screen.getByRole('button', { name: 'Add transformer' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/must match the two buses/)).toBeTruthy()
  })

  it('Escape and Cancel close it', async () => {
    const { onClose } = mount()
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
