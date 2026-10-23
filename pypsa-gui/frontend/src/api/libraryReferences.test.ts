import { beforeEach, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
vi.mock('./client', () => ({ default: { get, post } }))
const { libraryReferencesApi } = await import('./libraryReferences')

beforeEach(() => { get.mockReset(); post.mockReset() })

it('previews through the read route and applies through the network edit route', async () => {
  post.mockResolvedValue({ data: {} })
  const args = { component_class: 'Link' as const, name: 'electrolyser',
    technology: 'electrolysis-aec-100mw', projection_year: 2030, confirm: [] }
  await libraryReferencesApi.preview(args)
  expect(post).toHaveBeenLastCalledWith('/library_reference/preview', args, { skipErrorToast: true })
  const approved = { ...args, digest: 'shown-card' }
  await libraryReferencesApi.apply(approved)
  expect(post).toHaveBeenLastCalledWith('/network/library_reference/apply', approved, { skipErrorToast: true })
})
