// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Template-e2e 06: the backup inputs' one editing home is section 5.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { campusApi, type IslandConfig } from '../../../api/campusElectrical'
import { useUIStore } from '../../../store/uiStore'
import { BackupInputs } from './BackupInputs'

vi.mock('../../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, island: vi.fn(), patchIsland: vi.fn() } }
})

const CONFIG: IslandConfig = {
  units: {},
  requirements: {
    scenarios: ['ups_bridged'],
    sustained_h: { value: 4, source: 'assumed' },
    bridge_s: { value: 30, source: 'datasheet' },
    fuel_autonomy_h: { value: 48, source: 'datasheet' },
    genset_redundancy_n: { value: 1, source: 'datasheet' },
  },
}
const state = (config: IslandConfig | null) =>
  ({ config, revision: 'rev1', study: null, presets: [], services: [], warnings: [] }) as never

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(campusApi.island).mockResolvedValue(state(CONFIG))
  vi.mocked(campusApi.patchIsland).mockResolvedValue(state(CONFIG))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BackupInputs /></QueryClientProvider>)
}

describe('BackupInputs (section 5)', () => {
  it('sends a backup edit as plain numbers with the revision', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-backup-edit'))
    const ups = screen.getByLabelText('UPS bridge (s)')
    await user.clear(ups)
    await user.type(ups, '60')
    await user.click(screen.getByTestId('island-backup-save'))
    await waitFor(() => expect(campusApi.patchIsland).toHaveBeenCalledWith(
      'Demo', { requirements: { bridge_s: 60 } }, 'rev1'))
  })

  it('refuses a negative backup number with a sentence opening with its name', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByTestId('island-backup-edit'))
    const fuel = screen.getByLabelText('Fuel autonomy (h)')
    await user.clear(fuel)
    await user.type(fuel, '-2')
    expect(screen.getByRole('alert').textContent).toBe('Fuel autonomy: -2 is below 0.')
    expect((screen.getByTestId('island-backup-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('is absent when the project has no island config', async () => {
    vi.mocked(campusApi.island).mockResolvedValue(state(null))
    mount()
    await waitFor(() => expect(campusApi.island).toHaveBeenCalled())
    expect(screen.queryByTestId('backup-inputs')).toBeNull()
  })
})
