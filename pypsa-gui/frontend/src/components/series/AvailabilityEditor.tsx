// The availability editor (TS-02): planned maintenance, must-run floors and
// temperature derating for a generator or a link, previewed before Apply,
// then optional refinement (TS-04). Host-agnostic like LoadSeriesEditor: the
// asset's Properties panel and the Time Series drawer mount the same thing.
//
// Forced outages are not generated here. They are sampled in the reliability
// study, never written into the dispatch, so this shows that read-only and
// leads to the asset's own outage inputs (rate and repair time, in its
// Properties form), with the study as a secondary link (UX review
// 2026-10-20, gatekeeper rule).
//
// Guided shows the key parameters (a season and a number of weeks, one
// share, one floor); dated windows, seasonal shares and the ambient model
// sit under Advanced, open by default in Expert.
import { useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { Btn, Seg } from '../PageKit'
import { errorText, seriesApi, type PendingSeries } from '../../api/profiles'
import { useUIStore } from '../../store/uiStore'
import {
  openFlowTarget, openFlowTargetInPlace, openSectionInExpert,
} from '../../layout/flowNavigation'
import {
  CurrentSeriesLine, DisabledReason, INPUT, MeasuredFileTab, PendingCard, RefinePanel, type EditorMode,
} from './SeriesKit'

type Kind = 'maintenance' | 'must_run' | 'derating' | 'forced_outages'

const KIND_LABELS: Record<Kind, string> = {
  maintenance: 'Maintenance',
  must_run: 'Must-run',
  derating: 'Derating',
  forced_outages: 'Forced outages',
}

export interface AvailabilityEditorProps {
  project: string
  component: string
  componentType: 'generators' | 'links'
  mode: EditorMode
}

interface Window { from: string; to: string; available: string }

const pct = (s: string) => Number(s) / 100
const num = (s: string) => (s === '' ? undefined : Number(s))

/** Open the asset's forced-outage rate the way every flow item's field opens
 *  (in place over the card in Guided, its Properties in Expert) and bring it
 *  into view, focused: the in-form scroll the outage note leads to. */
export function revealOutageInputs(type: 'Generator' | 'Link', name: string) {
  const ui = useUIStore.getState()
  const target = { section: 'assets' as const, panel: null, component: type, entity: name,
    field: 'outage_rate_value' }
  if (ui.uiMode === 'guided') openFlowTargetInPlace(target)
  else {
    openFlowTarget(target)
    ui.requestPropertiesEdit({ type, name })
  }
  let tries = 0
  const find = () => {
    const label = Array.from(document.querySelectorAll('label'))
      .find(l => l.textContent?.startsWith('Forced-outage rate'))
    if (label) {
      label.scrollIntoView({ block: 'center' })
      label.querySelector('input')?.focus()
    } else if (++tries < 30) {
      requestAnimationFrame(find)
    }
  }
  requestAnimationFrame(find)
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 text-muted">{label}</span>
      {children}
    </div>
  )
}

export default function AvailabilityEditor({ project, component, componentType, mode }: AvailabilityEditorProps) {
  const [kind, setKind] = useState<Kind>('maintenance')
  const [advanced, setAdvanced] = useState(mode === 'expert')
  const [pending, setPending] = useState<PendingSeries | null>(null)
  // Generate the kind's series, or read a measured file for its field.
  const [from, setFrom] = useState<'generate' | 'measured'>('generate')
  // maintenance
  const [season, setSeason] = useState<'summer' | 'winter'>('summer')
  const [weeks, setWeeks] = useState('2')
  const [dated, setDated] = useState(false)
  const [windows, setWindows] = useState<Window[]>([{ from: '', to: '', available: '0' }])
  // must-run
  const [share, setShare] = useState('')
  const [seasonal, setSeasonal] = useState(false)
  const [winter, setWinter] = useState('')
  const [summer, setSummer] = useState('')
  // derating
  const [floor, setFloor] = useState('70')
  const [threshold, setThreshold] = useState('')
  const [perK, setPerK] = useState('')

  // The reliability study is the Results section's adequacy tab: opened
  // through the shared section navigation, in the mode the user is in. Guided
  // opens the Results panel itself, not the hub card, and stays Guided.
  const openReliability = () => {
    const ui = useUIStore.getState()
    if (ui.uiMode === 'guided') ui.setSlidePanel('results')
    else openSectionInExpert('results')
    ui.requestResultsTab('adequacy')
  }
  const editOutageInputs = () =>
    revealOutageInputs(componentType === 'links' ? 'Link' : 'Generator', component)

  function params(): Record<string, unknown> {
    switch (kind) {
      case 'maintenance':
        return dated
          ? { windows: windows.map(w => ({ from: w.from, to: w.to, available: pct(w.available) })) }
          : { rule: { season, weeks: Math.trunc(Number(weeks)) } }
      case 'must_run':
        return seasonal ? { winter_share: pct(winter), summer_share: pct(summer) } : { share: pct(share) }
      default: {
        const p: Record<string, unknown> = { floor: pct(floor) }
        if (num(threshold) != null) p.threshold_c = num(threshold)
        if (num(perK) != null) p.per_k = Number(perK) / 100
        return p
      }
    }
  }

  const missing = kind === 'maintenance'
    ? (dated ? (windows.every(w => w.from && w.to && w.available !== '') ? null
      : 'Give each window its dates and availability to preview.')
      : weeks ? null : 'Enter the weeks of maintenance to preview.')
    : kind === 'must_run'
      ? (seasonal ? (winter && summer ? null : 'Enter the winter and summer shares to preview.')
        : share ? null : 'Enter the must-run share to preview.')
      : floor ? null : 'Enter the derating floor to preview.'
  const ready = missing == null

  const generate = useMutation({
    mutationFn: () => seriesApi.generateAvailability(project, {
      kind, components: [component], component_type: componentType, params: params(),
    }),
    onSuccess: setPending,
  })

  const setWindow = (i: number, patch: Partial<Window>) =>
    setWindows(windows.map((w, j) => (j === i ? { ...w, ...patch } : w)))

  return (
    <div className="text-xs" data-testid="availability-editor">
      <Seg value={kind} onChange={v => { setKind(v); setPending(null) }}
        options={(Object.keys(KIND_LABELS) as Kind[]).map(k => ({ value: k, label: KIND_LABELS[k] }))} />

      {kind !== 'forced_outages' && (
        <div className="mt-2">
          <CurrentSeriesLine project={project} componentType={componentType}
            attribute={kind === 'must_run' ? 'p_min_pu' : 'p_max_pu'} component={component}
            onPending={p => { setPending(p); setAdvanced(true) }} />
          <Seg value={from} onChange={v => { setFrom(v); setPending(null) }}
            options={[{ value: 'generate', label: 'Generate' }, { value: 'measured', label: 'Measured file' }]} />
        </div>
      )}
      {kind === 'forced_outages' ? (
        <div className="mt-2 border border-border rounded p-2" data-testid="forced-outages-note">
          <p className="text-muted text-[11px]">
            The dispatch keeps planned availability only, so an outage is never counted twice.
          </p>
          <div className="flex flex-col items-start gap-1 mt-1">
            <Btn onClick={editOutageInputs}><Pencil size={12} /> Edit the outage rate and repair time</Btn>
            <button type="button" className="text-[11px] text-accent hover:underline text-left" onClick={openReliability}>
              Forced outages are sampled in the reliability study ›
            </button>
          </div>
        </div>
      ) : from === 'measured' ? (
        <MeasuredFileTab project={project} componentType={componentType}
          attribute={kind === 'must_run' ? 'p_min_pu' : 'p_max_pu'} component={component} onPending={setPending} />
      ) : (
        <div className="mt-2 space-y-1">
          {kind === 'maintenance' && (dated ? (
            <div className="space-y-1" data-testid="maintenance-windows">
              {windows.map((w, i) => (
                <div key={i} className="flex items-center gap-1">
                  <input aria-label={`Window ${i + 1} from`} className={`${INPUT} w-24`} placeholder="MM-DD"
                    value={w.from} onChange={e => setWindow(i, { from: e.target.value })} />
                  <span className="text-muted">to</span>
                  <input aria-label={`Window ${i + 1} to`} className={`${INPUT} w-24`} placeholder="MM-DD"
                    value={w.to} onChange={e => setWindow(i, { to: e.target.value })} />
                  <input aria-label={`Window ${i + 1} available %`} className={`${INPUT} w-14`}
                    value={w.available} onChange={e => setWindow(i, { available: e.target.value })} />
                  <span className="text-muted">% available</span>
                  {windows.length > 1 && (
                    <button type="button" aria-label={`Remove window ${i + 1}`} className="text-muted hover:text-danger"
                      onClick={() => setWindows(windows.filter((_, j) => j !== i))}><Trash2 size={11} /></button>
                  )}
                </div>
              ))}
              <Btn onClick={() => setWindows([...windows, { from: '', to: '', available: '0' }])}>
                <Plus size={12} /> Window
              </Btn>
            </div>
          ) : (
            <>
              <Row label="Season">
                <Seg value={season} onChange={setSeason}
                  options={[{ value: 'summer', label: 'Summer' }, { value: 'winter', label: 'Winter' }]} />
              </Row>
              <Row label="Weeks off">
                <input aria-label="Weeks off" className={`${INPUT} w-16`} inputMode="numeric"
                  value={weeks} onChange={e => setWeeks(e.target.value)} />
              </Row>
            </>
          ))}

          {kind === 'must_run' && (seasonal ? (
            <>
              <Row label="Winter floor %">
                <input aria-label="Winter floor %" className={`${INPUT} w-16`} value={winter} onChange={e => setWinter(e.target.value)} />
              </Row>
              <Row label="Summer floor %">
                <input aria-label="Summer floor %" className={`${INPUT} w-16`} value={summer} onChange={e => setSummer(e.target.value)} />
              </Row>
            </>
          ) : (
            <Row label="Floor %">
              <input aria-label="Floor %" className={`${INPUT} w-16`} value={share} onChange={e => setShare(e.target.value)} />
            </Row>
          ))}

          {kind === 'derating' && (
            <Row label="Lowest output %">
              <input aria-label="Lowest output %" className={`${INPUT} w-16`} value={floor} onChange={e => setFloor(e.target.value)} />
            </Row>
          )}

          <button type="button" className="block text-[11px] text-accent" onClick={() => setAdvanced(!advanced)}>
            {advanced ? 'Hide advanced' : 'Advanced'}
          </button>
          {advanced && (
            <div className="space-y-1" data-testid="availability-advanced">
              {kind === 'maintenance' && (
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={dated} onChange={e => setDated(e.target.checked)} />
                  Dated windows instead of a season
                </label>
              )}
              {kind === 'must_run' && (
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={seasonal} onChange={e => setSeasonal(e.target.checked)} />
                  Winter and summer floors (heating-led)
                </label>
              )}
              {kind === 'derating' && (
                <>
                  <Row label="Rated at °C">
                    <input aria-label="Rated at °C" className={`${INPUT} w-16`} placeholder="15"
                      value={threshold} onChange={e => setThreshold(e.target.value)} />
                  </Row>
                  <Row label="Loss % per K">
                    <input aria-label="Loss % per K" className={`${INPUT} w-16`} placeholder="0.7"
                      value={perK} onChange={e => setPerK(e.target.value)} />
                  </Row>
                </>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Btn variant="primary" onClick={() => generate.mutate()} disabled={!ready || generate.isPending}>
              <Play size={12} /> Preview
            </Btn>
            <DisabledReason reason={missing} />
          </div>
        </div>
      )}

      {generate.error && <p className="text-[11px] text-red-600 mt-1" data-testid="editor-error">{errorText(generate.error)}</p>}

      {pending && kind !== 'forced_outages' && (
        <>
          <PendingCard project={project} component={component} pending={pending} power={false}
            attribute={kind === 'must_run' ? 'p_min_pu' : 'p_max_pu'} />
          {advanced && <RefinePanel project={project} pending={pending} power={false} onRefined={setPending} />}
        </>
      )}
    </div>
  )
}
