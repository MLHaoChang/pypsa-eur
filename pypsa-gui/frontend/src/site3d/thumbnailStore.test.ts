// Which thumbnail the hub card re-reads (visual-layers plan 3 S5). A bump is
// per project and site: crossing them would cache-bust the wrong image, or
// leave a replaced capture on the old URL.
import { beforeEach, describe, expect, it } from 'vitest'
import { thumbnailKey, useThumbnailStore } from './thumbnailStore'

beforeEach(() => useThumbnailStore.getState().resetForTests())

describe('thumbnail versions', () => {
  it('keys an unsaved project apart from a named one', () => {
    expect(thumbnailKey(null, 'site-a')).toBe('__local__/site-a')
    expect(thumbnailKey('alpha', 'site-a')).toBe('alpha/site-a')
    expect(thumbnailKey('alpha', 'site-a')).not.toBe(thumbnailKey('alpha', 'site-b'))
  })

  it('bumps one project and site without moving the others', () => {
    const { bump } = useThumbnailStore.getState()
    bump('alpha', 'site-a')
    bump('alpha', 'site-a')
    bump(null, 'site-a')
    bump('beta', 'site-a')
    const versions = useThumbnailStore.getState().versions
    expect(versions[thumbnailKey('alpha', 'site-a')]).toBe(2)
    expect(versions[thumbnailKey(null, 'site-a')]).toBe(1)
    expect(versions[thumbnailKey('beta', 'site-a')]).toBe(1)
    expect(versions[thumbnailKey('alpha', 'site-b')]).toBeUndefined()
  })
})
