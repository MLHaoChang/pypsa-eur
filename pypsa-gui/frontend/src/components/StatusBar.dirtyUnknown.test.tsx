import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StatusBar from './StatusBar'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network')
vi.mock('../api/simulation')

// ADR-0001 at the save-state indicator. `dirty` was `(undoInfo?.depth ?? 0) > 0`,
// so a FAILED undo-info poll produced the same value as a genuinely clean
// project: green dot, "Project is up-to-date". That is a claim about whether
// the user's work is safe, asserted from an answer that was never obtained.
function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <StatusBar />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Alpha', projectName: 'Alpha' })
  vi.mocked(networkApi.getMeta).mockResolvedValue({ bus_count: 1 } as never)
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 24 } as never)
  vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'idle' } as never)
})

describe('StatusBar save-state indicator', () => {
  it('does not claim the project is up-to-date when the undo poll fails', async () => {
    vi.mocked(networkApi.undoInfo).mockRejectedValue(new Error('refused'))
    renderBar()
    await waitFor(() => {
      expect(screen.queryByTitle('Project is up-to-date')).toBeNull()
    })
    // and says so explicitly rather than rendering nothing
    expect(screen.getByTitle(/could not|unknown/i)).toBeTruthy()
  })

  it('still reports a genuinely clean project as up-to-date', async () => {
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0 } as never)
    renderBar()
    await waitFor(() => {
      expect(screen.getByTitle('Project is up-to-date')).toBeTruthy()
    })
  })

  it('still reports real unsaved edits', async () => {
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 3 } as never)
    renderBar()
    await waitFor(() => {
      expect(screen.getByTitle('3 unsaved edits')).toBeTruthy()
    })
  })
})
