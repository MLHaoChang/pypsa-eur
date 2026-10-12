import { afterEach, expect, it, vi } from 'vitest'
import { client } from './client'
import { getCheckedAnswerCapabilities } from './reliability'

afterEach(() => vi.restoreAllMocks())
it('reads capabilities through the authenticated client without noisy background errors', async () => {
  const data = { version: 1, mode: 'pilot', profiles: { 'storage-unit-cost/1': { enabled: true } } }
  const get = vi.spyOn(client, 'get').mockResolvedValue({ data })
  expect(await getCheckedAnswerCapabilities()).toEqual(data)
  expect(get).toHaveBeenCalledWith('/chat/reliability/capabilities', { skipErrorToast: true })
})
it.each([null, { version: 2, mode: 'pilot', profiles: {} }, { version: 1, mode: 'invented', profiles: {} },
  { version: 1, mode: 'pilot' }, { version: 1, mode: 'pilot', profiles: [] },
  { version: 1, mode: 'pilot', profiles: { fake: { enabled: 'yes' } } },
  { version: 1, mode: 'pilot', profiles: { fake: null } },
  { version: 1, mode: 'pilot', profiles: Object.fromEntries(Array.from({ length: 33 }, (_, i) => [i, { enabled: true }])) },
])('does not treat malformed availability as an opt-in capability: %j', async data => {
  vi.spyOn(client, 'get').mockResolvedValue({ data })
  await expect(getCheckedAnswerCapabilities()).rejects.toThrow('could not be verified')
})
