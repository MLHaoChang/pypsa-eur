import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

describe('ConfirmDialog', () => {
  it('renders message and fires onConfirm', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <ConfirmDialog open title="Delete project" message="Delete 'Alpha'? This removes its files from disk."
        confirmLabel="Delete" danger onConfirm={onConfirm} onCancel={() => {}} />,
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText(/removes its files/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('disables both buttons and blocks Escape while pending', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    render(
      <ConfirmDialog open title="Delete project" message="working" confirmLabel="Delete"
        pending onConfirm={() => {}} onCancel={onCancel} />,
    )
    const confirmButton = screen.getByRole('button', { name: /Working/ }) as HTMLButtonElement
    const cancelButton = screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement
    expect(confirmButton.disabled).toBe(true)
    expect(cancelButton.disabled).toBe(true)
    await user.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('renders nothing when closed', () => {
    render(
      <ConfirmDialog open={false} title="x" message="y" confirmLabel="z"
        onConfirm={() => {}} onCancel={() => {}} />,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
