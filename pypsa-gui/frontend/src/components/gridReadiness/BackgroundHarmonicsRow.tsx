// "Background harmonics at the PCC" under Connection limits (grid-compliance 29): the field measurement of the
// voltage distortion already on the grid, which the next campus run adds to the plant's own. The file is
// checked on upload; the Results "Harmonic emission" section says what the run took.
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { campusApi, errorText, type BackgroundHarmonics } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'

const KEY = (project: string) => ['campusElectrical', 'background_harmonics', project] as const
const STATE_KEY = (project: string) => ['campusElectrical', 'state', project] as const

/** The stored line: "·"-separated data, an ISO date, and whether the latest run took it. */
export function storedLine(b: Extract<BackgroundHarmonics, { has_background: true }>, hasRun: boolean): string {
  const parts = [b.source, `measured ${b.measured_on}`]
  if (b.filename) parts.push(b.filename)
  parts.push(`${b.orders} ${b.orders === 1 ? 'order' : 'orders'}`)
  if (b.in_latest_run) parts.push('used by the latest run')
  else if (!hasRun) parts.push('no run yet')
  return parts.join(' · ')
}

export default function BackgroundHarmonicsRow() {
  const project = useUIStore(s => s.currentProject) ?? ''
  const qc = useQueryClient()
  const stored = useQuery({ queryKey: KEY(project), queryFn: () => campusApi.backgroundHarmonics(project),
    enabled: !!project, retry: false })
  const state = useQuery({ queryKey: STATE_KEY(project), queryFn: () => campusApi.state(project),
    enabled: !!project, retry: false })
  const hasRun = state.data?.results != null
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [source, setSource] = useState('')
  const [measuredOn, setMeasuredOn] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: KEY(project) }),
    qc.invalidateQueries({ queryKey: STATE_KEY(project) })])
  const upload = useMutation({
    mutationFn: () => campusApi.putBackgroundHarmonics(project, file!, source.trim(), measuredOn.trim()),
    onMutate: () => setError(null),
    onSuccess: () => { setFile(null); setSource(''); setMeasuredOn(''); if (fileRef.current) fileRef.current.value = ''; return refresh() },
    onError: (e: unknown) => setError(errorText(e)),
  })
  const remove = useMutation({
    mutationFn: () => campusApi.deleteBackgroundHarmonics(project),
    onMutate: () => setError(null),
    onSuccess: () => { setConfirming(false); return refresh() },
    onError: (e: unknown) => setError(errorText(e)),
  })
  const b = stored.data
  const busy = upload.isPending || remove.isPending
  return (
    <li data-testid="connection-limit-background_harmonics" className="text-[12px]">
      <span className="font-semibold">Background harmonics at the PCC</span>
      {stored.isError ? (
        <span className="block text-warn" data-testid="background-error">
          Background harmonics: the stored measurement could not be read.</span>
      ) : b?.has_background ? (
        <>
          <span className="block text-muted" data-testid="background-stored">{storedLine(b, hasRun)}</span>
          {hasRun && !b.in_latest_run && (
            <span className="block text-warn" data-testid="background-out-of-date">
              Out of date: the background changed after the last run.</span>
          )}
        </>
      ) : (
        <span className="block text-muted" data-testid="background-empty">
          None: the run uses the plant's own distortion alone.</span>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px]">
        <label className="text-muted" htmlFor="bg-source">Source:</label>
        <input id="bg-source" value={source} maxLength={200} onChange={e => setSource(e.target.value)}
          placeholder="Who measured it" className="min-w-[8rem] rounded border border-border bg-bg px-1 py-0.5" />
        <label className="text-muted" htmlFor="bg-date">Measured on:</label>
        <input id="bg-date" type="date" value={measuredOn} onChange={e => setMeasuredOn(e.target.value)}
          className="rounded border border-border bg-bg px-1 py-0.5" />
        <label className="text-muted" htmlFor="bg-file">File:</label>
        <input id="bg-file" ref={fileRef} type="file" accept=".csv,text/csv"
          onChange={e => setFile(e.target.files?.[0] ?? null)} className="max-w-[14rem]" />
      </div>
      <span className="block text-[11px] text-muted">
        A CSV with the harmonic order and its voltage in % of the nominal fundamental, up to 1 MiB.</span>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" disabled={!file || !source.trim() || !measuredOn || busy} onClick={() => upload.mutate()}
          className="rounded border border-border px-2 py-0.5 disabled:opacity-50">
          {b?.has_background ? 'Replace the measurement' : 'Save the measurement'}
        </button>
        {b?.has_background && !confirming && (
          <button type="button" disabled={busy} onClick={() => setConfirming(true)} className="underline">Remove</button>
        )}
        {b?.has_background && confirming && (
          <span role="group" aria-label="Remove the measurement" className="inline-flex items-center gap-2">
            <span>Remove the stored measurement?</span>
            <button type="button" disabled={busy} onClick={() => remove.mutate()} className="underline text-warn">Remove it</button>
            <button type="button" onClick={() => setConfirming(false)} className="underline">Keep it</button>
          </span>
        )}
      </div>
      {error && <p role="alert" className="text-[11px] text-warn" data-testid="background-upload-error">{error}</p>}
    </li>
  )
}
