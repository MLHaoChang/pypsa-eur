// ENTSO-E day-ahead prices (SCU06 S1): fetch one complete year of a bidding
// zone's prices into the org Library, where a business case attaches it like any
// price series. The one entry point: the section-7 price card (SCU03 K1) opens or
// calls this same action rather than adding a second fetch.
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { marketPricesApi, type DayAheadResult } from '../../../api/marketPrices'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../../../utils/mutationGuard'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'

/** The server's plain refusal; never a status code or ENTSO-E's own text. */
function refusalText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  if (typeof detail === 'string') return detail
  return 'The prices could not be fetched. Try again later.'
}

function TokenForm({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setStatus(null)
    try {
      await marketPricesApi.saveToken(value.trim())
      setValue('')
      setStatus({ ok: true, text: 'Access token saved.' })
      onSaved()
    } catch (err) {
      setStatus({ ok: false, text: refusalText(err) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={save} data-testid="da-token-form">
      <label htmlFor="da-token">ENTSO-E access token</label>
      <input id="da-token" type="password" className={input} value={value} autoComplete="off"
             spellCheck={false} onChange={e => setValue(e.target.value)} data-testid="da-token-input" />
      <button type="submit" className="underline" disabled={!value.trim() || busy}>
        {busy ? 'Saving…' : 'Save token'}</button>
      {status && <p role="status" className={status.ok ? 'text-success' : 'text-danger'}>{status.text}</p>}
    </form>
  )
}

export function DayAheadPrices() {
  const project = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const qc = useQueryClient()
  const key = nk(project, 'market-prices')
  const info = useQuery({ queryKey: key, enabled: !!project,
                          queryFn: () => marketPricesApi.project(project as string) })
  const [zone, setZone] = useState('')
  const [year, setYear] = useState('')
  const [fetching, setFetching] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [landed, setLanded] = useState<DayAheadResult | null>(null)
  // The token is the workspace's: removing it asks first, inline (UX 27 on S1c).
  const [confirmClear, setConfirmClear] = useState(false)

  const data = info.data
  useEffect(() => {
    if (!data) return
    setZone(z => z || data.zone || '')
    setYear(y => y || String(data.last_year))
  }, [data])

  if (!project || !data) return null
  const picked = data.zones.find(z => z.code === zone)
  const zoneName = (code: string) => {
    const z = data.zones.find(x => x.code === code)
    return z ? `${z.code} · ${z.label}` : code
  }
  const first = picked?.first_year ?? 2015
  const y = Number(year)
  const yearError = year.trim() === '' || !Number.isInteger(y) || y < first || y > data.last_year
    ? `Year: choose a complete year from ${first} to ${data.last_year}.` : null
  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : null

  const fetchPrices = async () => {
    if (!picked || yearError) return
    setFetching(true); setRefusal(null); setLanded(null)
    try {
      const out = await marketPricesApi.fetchDayAhead(project, picked.code, y)
      setLanded(out)
      await qc.invalidateQueries({ queryKey: nk(project, 'library', 'series') })
      await qc.invalidateQueries({ queryKey: key })
    } catch (e) {
      setRefusal(refusalText(e))
    } finally {
      setFetching(false)
    }
  }

  return (
    <fieldset className="space-y-1 border border-border rounded p-1" data-testid="da-prices">
      <legend>Day-ahead prices from ENTSO-E</legend>
      {!data.token_set && (
        <div className="space-y-1">
          <p data-testid="da-no-token">Day-ahead prices need an ENTSO-E access token.
            {!data.can_set_token && ' Ask a super-admin to add it.'}</p>
          {data.can_set_token && <TokenForm onSaved={() => void qc.invalidateQueries({ queryKey: key })} />}
        </div>
      )}
      {data.token_set && data.can_fetch && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col">Bidding zone
            <select className={input} value={zone} onChange={e => setZone(e.target.value)}
                    data-testid="da-zone">
              {!zone && <option value="">choose a zone…</option>}
              {data.zones.map(z => <option key={z.code} value={z.code}>{z.code} · {z.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col">Year
            <input className={`${input} w-20`} inputMode="numeric" value={year} aria-invalid={!!yearError}
                   aria-describedby="da-year-hint" onChange={e => setYear(e.target.value)}
                   data-testid="da-year" />
          </label>
          <button type="button" className="underline disabled:opacity-40" data-testid="da-fetch"
                  disabled={fetching || !picked || !!yearError || !!blocked} onClick={fetchPrices}>
            Fetch day-ahead prices</button>
        </div>
      )}
      {data.token_set && data.can_fetch && (
        <div className="text-muted space-y-0.5">
          <p>{data.pin_zone
            ? (zone === data.pin_zone ? 'Zone from the site pin.' : `The site pin is in ${zoneName(data.pin_zone)}.`)
            : 'The site pin does not settle the zone: choose one.'}</p>
          {yearError && <p id="da-year-hint" className="text-danger">{yearError}</p>}
          {blocked && <p>{blocked}</p>}
        </div>
      )}
      {data.token_set && !data.can_fetch && (
        <p className="text-muted">You can view this project but not add prices to its Library.</p>
      )}
      {fetching && picked && (
        <p role="status">Fetching {picked.code} day-ahead prices for {y}…</p>
      )}
      {refusal && <p role="alert" className="text-danger" data-testid="da-refusal">{refusal}</p>}
      {landed && (
        <p role="status" className="text-success" data-testid="da-landed">
          Stored as {landed.ref.id} v{landed.ref.version}: {landed.source} · {landed.zone} · {landed.year}
          {' '}· fetched {landed.fetched} · EUR/MWh ({landed.year} EUR)
        </p>
      )}
      {data.token_set && data.can_set_token && !confirmClear && (
        <button type="button" className="underline text-muted" data-testid="da-token-clear"
                onClick={() => setConfirmClear(true)}>Remove the access token</button>
      )}
      {data.token_set && data.can_set_token && confirmClear && (
        <p className="text-muted" data-testid="da-token-confirm">Remove for everyone?{' '}
          <button type="button" className="underline text-danger"
                  onClick={async () => {
                    setConfirmClear(false)
                    try {
                      await marketPricesApi.clearToken()
                      await qc.invalidateQueries({ queryKey: key })
                    } catch (e) {
                      setRefusal(refusalText(e))
                    }
                  }}>Remove</button>{' · '}
          <button type="button" className="underline" onClick={() => setConfirmClear(false)}>Keep</button>
        </p>
      )}
    </fieldset>
  )
}
