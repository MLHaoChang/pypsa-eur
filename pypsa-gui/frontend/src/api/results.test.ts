// The study-why read names no project: the route compares `project_id` with the open project's UUID, and the
// frontend knows the project by name only (a name is refused 409 project_not_open; PR #1724 shots).
import { describe, expect, it, vi } from 'vitest'
import { client } from './client'
import { getEhStudyWhy } from './results'

vi.mock('./client', () => ({ client: { get: vi.fn() } }))

describe('getEhStudyWhy', () => {
  it('reads the open project, sending no project_id', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: { steps: [] } })
    await getEhStudyWhy('verdict')
    expect(client.get).toHaveBeenCalledWith('/results/eh_study/why/verdict', { skipErrorToast: true })
  })
})
