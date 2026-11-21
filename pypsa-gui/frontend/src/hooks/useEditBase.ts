import { useEffect, useRef } from 'react'

/**
 * The base of an edit (05g). An asset card's builder maps the form to a patch. `updateAsset` needs to know which keys
 * of that patch the user changed, so it compares against what the SAME builder returns for the form's initial values:
 * both sides take the same scaling, so an untouched field is bit-equal. The builder is registered on every render
 * (`remember`) and run once, when the editor opens: the form is then still the seeded one. `initial()` hands it to
 * `updateAsset` at save time. If the builder throws at open (row gone), there is no base and `updateAsset` falls
 * back to comparing against the cached row.
 */
export function useEditBase<T, L>(open: boolean, row: T, live: L, ready = true) {
  const latest = useRef<((current: T) => Partial<T>) | null>(null)
  const base = useRef<Partial<T> | undefined>(undefined)
  // What the form was built on when the editor opened (the schema's units and scales). A typed cost is scaled under
  // THIS at save, so the basis claimed is the one the user saw, even if the asset's bus unit changed since. Taken once
  // per opening, as soon as the schema is there (`ready`).
  const frozen = useRef<L>(live)
  const taken = useRef(false)
  const liveRef = useRef(live)
  liveRef.current = live
  useEffect(() => {
    if (!open) { taken.current = false; base.current = undefined; return }
    if (taken.current || !ready) return
    taken.current = true
    frozen.current = liveRef.current
    try {
      base.current = latest.current ? latest.current(row) : undefined
    } catch { base.current = undefined }  // a builder that refuses (row gone) has no base: the cached row stands in
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ready])
  return {
    remember: (builder: (current: T) => Partial<T>) => { latest.current = builder; return builder },
    initial: () => base.current,
    /** The schema and specs the editor opened with (the live ones until it has opened). */
    atOpen: (): L => (taken.current ? frozen.current : liveRef.current),
  }
}
