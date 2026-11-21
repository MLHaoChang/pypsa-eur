import { useEffect, useRef } from 'react'

/**
 * The base of an edit (05g). An asset card's builder maps the form to a patch. `updateAsset` needs to know which keys
 * of that patch the user changed, so it compares against what the SAME builder returns for the form's initial values:
 * both sides take the same scaling, so an untouched field is bit-equal. The builder is registered on every render
 * (`remember`) and run once, when the editor opens: the form is then still the seeded one. `initial()` hands it to
 * `updateAsset` at save time.
 */
export function useEditBase<T>(open: boolean, row: T) {
  const latest = useRef<((current: T) => Partial<T>) | null>(null)
  const base = useRef<Partial<T> | undefined>(undefined)
  useEffect(() => {
    try {
      base.current = open && latest.current ? latest.current(row) : undefined
    } catch { base.current = undefined }  // a builder that refuses (row gone) has no base: the cached row stands in
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  return {
    remember: (builder: (current: T) => Partial<T>) => { latest.current = builder; return builder },
    initial: () => base.current,
  }
}
