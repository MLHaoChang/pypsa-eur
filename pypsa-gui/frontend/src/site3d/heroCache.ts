// A load-once cache for hero models whose state the scene reads (Phase 2
// plan Task 3.5). Not a suspending hook: r3f reports every render error —
// even one an error boundary caught — through reportError, i.e. as an
// uncaught page error, so a model that fails to load (offline, blocked)
// must never throw into React. The scene draws the parametric form until
// the state is 'ready' and keeps it for good on 'failed'. No three.

export type HeroState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; value: T }
  | { status: 'failed' }

export interface HeroCache<T> {
  /** The url's state; the same object until it changes. */
  get(url: string): HeroState<T>
  /** Start loading the url unless it already is (or was). */
  load(url: string): void
  subscribe(listener: () => void): () => void
}

const IDLE = { status: 'idle' } as const
const LOADING = { status: 'loading' } as const
const FAILED = { status: 'failed' } as const

export function createHeroCache<T>(loader: (url: string) => Promise<T>): HeroCache<T> {
  const states = new Map<string, HeroState<T>>()
  const listeners = new Set<() => void>()
  const set = (url: string, s: HeroState<T>) => { states.set(url, s); for (const l of listeners) l() }
  return {
    get: url => states.get(url) ?? IDLE,
    load(url) {
      if (states.has(url)) return
      set(url, LOADING)
      loader(url).then(
        value => set(url, { status: 'ready', value }),
        error => {
          console.warn('[site3d] a hero model failed; keeping the parametric form:', error instanceof Error ? error.message : error)
          set(url, FAILED)
        },
      )
    },
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener) } },
  }
}
