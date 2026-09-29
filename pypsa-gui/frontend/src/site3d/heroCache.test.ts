// Phase 2 plan Task 3.5: a hero that fails to load must leave the parametric
// form in place without throwing into React — r3f reports even an error a
// boundary caught as an uncaught page error — so loads go through a cache
// whose state the scene reads, never a suspending hook.
import { describe, it, expect, vi } from 'vitest'
import { createHeroCache } from './heroCache'

const flush = () => new Promise(r => setTimeout(r, 0))

describe('createHeroCache', () => {
  it('loads a url once, reports loading then ready, and notifies subscribers', async () => {
    const load = vi.fn(async (url: string) => `model:${url}`)
    const cache = createHeroCache(load)
    const seen = vi.fn()
    cache.subscribe(seen)
    expect(cache.get('/a.glb')).toEqual({ status: 'idle' })
    cache.load('/a.glb'); cache.load('/a.glb')
    expect(cache.get('/a.glb')).toEqual({ status: 'loading' })
    await flush()
    expect(cache.get('/a.glb')).toEqual({ status: 'ready', value: 'model:/a.glb' })
    expect(load).toHaveBeenCalledTimes(1)
    expect(seen).toHaveBeenCalledTimes(2)
  })
  it('a failed load becomes "failed" with no unhandled rejection, and is not retried', async () => {
    const unhandled = vi.fn()
    process.on('unhandledRejection', unhandled)
    const load = vi.fn(async () => { throw new Error('Failed to fetch') })
    const cache = createHeroCache(load)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    cache.load('/b.glb')
    await flush(); await flush()
    expect(cache.get('/b.glb')).toEqual({ status: 'failed' })
    cache.load('/b.glb')
    expect(load).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledOnce()
    expect(unhandled).not.toHaveBeenCalled()
    process.off('unhandledRejection', unhandled)
    warn.mockRestore()
  })
  it('get returns a stable object between changes (useSyncExternalStore needs it)', async () => {
    const cache = createHeroCache(async () => 1)
    expect(cache.get('/c.glb')).toBe(cache.get('/c.glb'))
    cache.load('/c.glb')
    const loading = cache.get('/c.glb')
    expect(cache.get('/c.glb')).toBe(loading)
    await flush()
    expect(cache.get('/c.glb')).toBe(cache.get('/c.glb'))
  })
  it('unsubscribe stops notifications', async () => {
    const cache = createHeroCache(async () => 1)
    const seen = vi.fn()
    const off = cache.subscribe(seen)
    off()
    cache.load('/d.glb'); await flush()
    expect(seen).not.toHaveBeenCalled()
  })
})
