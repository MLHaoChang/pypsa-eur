// Expert › Results › Adequacy: the multi-objective front (DC review 01). It
// sits beside the cost-vs-availability frontier, which sweeps one axis; this
// one sweeps a reliability lever and a CO2 limit and samples each built fleet.
// The view itself is shared with the Guided section-10 tile (FrontView).
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, Square } from 'lucide-react'
import { frontApi, type FrontLever } from '../../api/front'
import { useUIStore } from '../../store/uiStore'
import { useStudyFinishedInvalidation } from '../../hooks/useStudyFinishedInvalidation'
import { frontKeys, FrontView, useFrontData } from './front/FrontView'
import { startRefusal } from './front/frontModel'

/** The `resultsSectionRequest` that opens this panel and scrolls it into view
 *  (the Guided tile's "Run the study in Expert ›"). */
export const FRONT_SECTION = 'front'

const LEVER_LABEL: Record<FrontLever, string> = {
  reserve_margin: 'Reserve margin (%)',
  ens_cap: 'ENS target (‱ of demand)',
}

/** "10, 15, 20" → numbers; null when a part is not a number. */
export function parseList(text: string): number[] | null {
  const parts = text.split(/[,\s]+/).filter(Boolean)
  const nums = parts.map(Number)
  return nums.every(Number.isFinite) ? nums : null
}

export function FrontPanel() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const keys = frontKeys(project)
  const { front, eh } = useFrontData()
  const [open, setOpen] = useState(false)
  const [lever, setLever] = useState<FrontLever>('reserve_margin')
  const [levels, setLevels] = useState('10, 15, 20')
  const [caps, setCaps] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const ref = useRef<HTMLElement>(null)
  const running = front?.status === 'running'
  useStudyFinishedInvalidation(front?.status ?? null)

  // Request-then-scroll: the Guided tile's link opens this panel and brings it into view.
  const sectionRequest = useUIStore(s => s.resultsSectionRequest)
  useEffect(() => {
    if (sectionRequest !== FRONT_SECTION) return
    setOpen(true)
    useUIStore.getState().clearResultsSectionRequest()
    requestAnimationFrame(() => ref.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }))
  }, [sectionRequest])

  const run = useMutation({
    mutationFn: () => {
      const lv = parseList(levels)
      const cp = caps.trim() ? parseList(caps) : []
      if (!lv || lv.length === 0) throw new Error('Reliability levels: enter numbers separated by commas.')
      if (!cp) throw new Error('CO₂ limits: enter numbers in t/yr separated by commas, or leave it empty.')
      return frontApi.start({
        lever,
        levels: lever === 'reserve_margin' ? lv.map(v => v / 100) : lv,
        // The uncapped column is always swept, so the reference emissions come free.
        co2_caps_t: [null, ...cp],
      })
    },
    onMutate: () => setFormError(null),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.front }),
    onError: (e: unknown) => setFormError(
      e instanceof Error && !(e as { response?: unknown }).response ? e.message : startRefusal(e)),
  })
  const abort = useMutation({
    mutationFn: frontApi.abort,
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.front }),
  })

  const field = 'h-[22px] rounded border border-border bg-bg px-1.5 text-[10px] text-text'
  return (
    <section ref={ref} className="border border-border rounded" data-testid="front-panel">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent">
        <Activity size={11} /> Cost, reliability and CO₂ front {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-2 text-[10px] text-muted">
            <label className="flex flex-col gap-0.5">
              Reliability lever
              <select className={field} value={lever} disabled={running} data-testid="front-lever"
                onChange={e => setLever(e.target.value as FrontLever)}>
                <option value="reserve_margin">Reserve margin</option>
                <option value="ens_cap">Unserved-energy (ENS) target</option>
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              {LEVER_LABEL[lever]}
              <input className={`${field} w-28`} value={levels} disabled={running} data-testid="front-levels"
                onChange={e => setLevels(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              CO₂ limits (t/yr), besides no limit
              <input className={`${field} w-36`} value={caps} disabled={running} placeholder="none" data-testid="front-caps"
                onChange={e => setCaps(e.target.value)} />
            </label>
            <button type="button" onClick={() => run.mutate()} disabled={running || run.isPending} data-testid="front-run"
              className="inline-flex items-center gap-1 h-[22px] px-2 border border-border rounded text-[10px] font-semibold text-text hover:border-accent hover:text-accent disabled:opacity-50">
              {running ? 'Studying…' : 'Run the study'}
            </button>
            {running && (
              <button type="button" onClick={() => abort.mutate()} data-testid="front-abort"
                className="inline-flex items-center gap-1 h-[22px] px-2 border border-border rounded text-[10px] text-muted hover:border-danger hover:text-danger">
                <Square size={9} /> Stop
              </button>
            )}
          </div>
          <p className="text-[10px] text-muted">
            One full solve per point, at most 16 in all. Stop keeps the points solved so far.
          </p>
          {formError && <p role="status" className="text-[11px] text-warn" data-testid="front-run-refusal">{formError}</p>}
          {front || eh ? (
            <FrontView front={front} eh={eh} testId="front-view"
              runStudy={{ label: 'Run the study', onClick: () => run.mutate() }} />
          ) : (
            <p className="text-[11px] text-muted" data-testid="front-not-run">
              Not run yet: set the levels above and run the study.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
