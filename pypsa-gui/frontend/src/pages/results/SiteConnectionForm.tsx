// The inline "Site connection" form (IC U1 follow-up b; owner decision): the
// commercial root every investment-case section needs. The point of
// connection is the one-way Link importing grid → site, the export Link the
// one-way Link site → grid, and the time zone the clock the tariff windows are
// read on. Likely Links are suggested (`siteConnection.ts`); the server checks
// and binds the whole commercial config on save, and a refusal is shown here.
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commercialApi, SaveRefusedError, SolverInFlightError, type SiteConnection } from '../../api/commercial'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { browserTimeZone, exportCandidates, oneWay, pocCandidates, timeZoneOptions } from './siteConnection'

export interface SiteConnectionFormProps {
  /** The stored root, or null when the project has no commercial config. */
  initial: SiteConnection | null
  onSaved?: () => void
  onCancel?: () => void
  /** The zone a NEW connection starts with (default: the browser's). */
  defaultTimeZone?: string
}

function saveError(e: unknown): string {
  if (e instanceof SolverInFlightError) return 'A solve is running; save the site connection after it finishes.'
  if (e instanceof SaveRefusedError) return e.message
  return 'The site connection could not be saved.'
}

export default function SiteConnectionForm({ initial, onSaved, onCancel, defaultTimeZone }:
                                           SiteConnectionFormProps) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const links = useQuery({ queryKey: nk(project, 'links'), queryFn: networkApi.getLinks })
  const meters = (links.data ?? []).filter(oneWay)
  const pocHints = pocCandidates(meters)
  // `null` = not touched: follow the stored value, else the first suggestion.
  const [poc, setPoc] = useState<string | null>(null)
  const pocValue = poc ?? initial?.poc_link ?? pocHints[0] ?? meters[0]?.name ?? ''
  const expHints = exportCandidates(meters, pocValue)
  const [exp, setExp] = useState<string | null>(null)
  const expValue = exp ?? (initial ? (initial.export_link ?? '') : (expHints[0] ?? ''))
  const [tz, setTz] = useState<string>(
    initial ? (initial.timezone ?? '') : (defaultTimeZone ?? browserTimeZone()))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const zones = timeZoneOptions(initial?.timezone, tz)

  const ordered = (hints: string[]) =>
    [...hints, ...meters.map(l => l.name).filter(n => !hints.includes(n))]
  const label = (name: string, hints: string[]) => {
    const l = meters.find(x => x.name === name)
    const route = l ? ` (${l.bus0} → ${l.bus1})` : ''
    return `${name}${route}${hints.includes(name) ? ', suggested' : ''}`
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!pocValue) return
    setSaving(true)
    setError(null)
    try {
      await commercialApi.saveSiteConnection({ poc_link: pocValue, export_link: expValue || null,
                                               timezone: tz || null })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await qc.invalidateQueries({ queryKey: nk(project, 'value_flows') })
      onSaved?.()
    } catch (err) {
      setError(saveError(err))
    } finally {
      setSaving(false)
    }
  }

  if (links.isPending) return <p className="text-[11px] text-muted py-2">Loading the network's Links…</p>
  if (links.isError) {
    return <p className="text-[11px] text-warn py-2" role="alert">
      The network's Links could not be read; reload to set the site connection.</p>
  }
  const field = 'text-[11px] border border-border rounded px-1 py-0.5 bg-surface'
  return (
    <form className="space-y-2 max-w-xl" onSubmit={submit} data-testid="site-connection-form"
          aria-label="Site connection">
      {meters.length === 0 && (
        <p className="text-[11px] text-warn" data-testid="site-connection-no-links">
          The network has no one-way Link to be the point of connection. Add a Link from the grid
          bus to the site (with p_min_pu ≥ 0), then set it here.</p>
      )}
      <div className="flex flex-col gap-0.5">
        <label htmlFor="sc-poc" className="text-[11px]">Point of connection (import Link, grid → site)</label>
        <select id="sc-poc" className={field} value={pocValue}
                onChange={e => { setPoc(e.target.value); setExp(null) }}>
          {ordered(pocHints).map(n => <option key={n} value={n}>{label(n, pocHints)}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-0.5">
        <label htmlFor="sc-export" className="text-[11px]">Export link (site → grid)</label>
        <select id="sc-export" className={field} value={expValue} onChange={e => setExp(e.target.value)}>
          <option value="">No export link (export is not priced)</option>
          {ordered(expHints).filter(n => n !== pocValue)
            .map(n => <option key={n} value={n}>{label(n, expHints)}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-0.5">
        <label htmlFor="sc-tz" className="text-[11px]">Site time zone</label>
        <select id="sc-tz" className={field} value={tz} onChange={e => setTz(e.target.value)}>
          <option value="">The snapshots are already site time</option>
          {zones.map(z => <option key={z} value={z}>{z}</option>)}
        </select>
        <p className="text-[10px] text-muted">
          With a zone set, the snapshots are read as UTC and the tariff's time windows on this clock.</p>
      </div>
      {error && <p className="text-[11px] text-warn" role="alert">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" className="text-[11px] px-2 py-0.5 border border-border rounded"
                disabled={saving || !pocValue}>
          {saving ? 'Saving…' : 'Save site connection'}
        </button>
        {onCancel && (
          <button type="button" className="text-[11px] underline" onClick={onCancel}>Cancel</button>
        )}
      </div>
    </form>
  )
}
