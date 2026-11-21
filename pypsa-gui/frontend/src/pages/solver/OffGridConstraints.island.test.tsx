import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OffGridConstraints, ISLAND_LIMITS_LABEL } from './OffGridConstraints'
import type { SolverConfig } from '../../api/types'

describe('Off-grid constraints: Island limits', () => {
  it('shows the island_constraints setting and patches it', async () => {
    const patch = vi.fn()
    render(<OffGridConstraints draft={{ island_constraints: false } as SolverConfig} patch={patch} />)
    const box = screen.getByLabelText(ISLAND_LIMITS_LABEL)
    expect(box).toHaveProperty('id', 'solver-island-constraints')
    await userEvent.click(box)
    expect(patch).toHaveBeenCalledWith({ island_constraints: true })
  })
})
