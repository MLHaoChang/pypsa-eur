// The site coordinate field (D01): the project's one confirmed site anchor,
// written by compare-and-swap on its revision. It lives in one home per mode
// (UI contract rule 4): the `site_anchor` drawer over the Project & site card
// in Guided, the horizon panel in Expert. It never creates a Bus or a Site;
// the anchor only admits "Create 3D site".
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { siteSceneApi, sceneErrorCopy, sceneErrorWantsReload, type SiteAnchorDoc } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { evaluateMutation } from '../utils/mutationGuard'
import NumberField from './NumberField'

export const SITE_ANCHOR_ROOT = 'site-anchor'
const SAVE_FIRST = 'Unsaved project: save it to keep site coordinates'

/** Client-side checks, worded as the server's refusals are mapped (no request for a value it would refuse). */
export function anchorInputErrors(lat: number, lng: number): { lat: string | null; lng: string | null } {
  const latOk = Number.isFinite(lat) && lat >= -90 && lat <= 90
  const lngOk = Number.isFinite(lng) && lng >= -180 && lng <= 180
  const swapped = !latOk && Number.isFinite(lat) && Math.abs(lat) <= 180 && Number.isFinite(lng) && Math.abs(lng) <= 90
  return {
    lat: latOk ? null : swapped ? 'Latitude must be between −90 and 90: did you swap latitude and longitude?'
      : 'Latitude: enter a number between −90 and 90',
    lng: lngOk ? null : 'Longitude: enter a number between −180 and 180',
  }
}

const fmt = (v: number) => v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')

/** "47.3769, 8.5417 · confirmed", or "Not set": the one-line summary a card shows. */
export function anchorSummary(doc: SiteAnchorDoc | undefined): string {
  const a = doc?.anchor
  if (!a) return 'Not set'
  return `${fmt(a.lat)}, ${fmt(a.lng)} · ${a.confirmed ? 'confirmed' : 'not confirmed'}`
}

export function useSiteAnchor() {
  const project = useUIStore(s => s.currentProject)
  return useQuery<SiteAnchorDoc>({
    queryKey: nk(project, SITE_ANCHOR_ROOT),
    queryFn: () => siteSceneApi.getAnchor(project as string),
    enabled: !!project,
    retry: false,
  })
}

export default function SiteAnchorField() {
  const project = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const request = useUIStore(s => s.siteAnchorRequest)
  const qc = useQueryClient()
  const q = useSiteAnchor()
  const [lat, setLat] = useState(NaN)
  const [lng, setLng] = useState(NaN)
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ copy: string; reload: boolean } | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  const server = q.data?.anchor ?? null
  useEffect(() => {
    // The server's value shows until the user types; a reload replaces it.
    if (touched) return
    setLat(server ? server.lat : NaN)
    setLng(server ? server.lng : NaN)
  }, [server, touched])

  // Request-then-scroll (as requestGridReadiness): clear once the field is on screen.
  useEffect(() => {
    if (!request || !ref.current) return
    ref.current.scrollIntoView({ block: 'start' })
    useUIStore.getState().clearSiteAnchorRequest()
  }, [request])

  const guard = evaluateMutation(readOnly, readOnlyReason)
  const blocked = !project ? SAVE_FIRST : guard.allowed ? null : guard.blockedMessage
  const errs = touched ? anchorInputErrors(lat, lng) : { lat: null, lng: null }
  const confirmedSame = !!server?.confirmed && server.lat === lat && server.lng === lng

  const write = async (anchor: SiteAnchorDoc['anchor']) => {
    if (!project || !q.data || blocked) return
    setBusy(true); setError(null)
    try {
      const doc = await siteSceneApi.putAnchor(project, anchor, q.data.revision)
      qc.setQueryData(nk(project, SITE_ANCHOR_ROOT), doc)
      void qc.invalidateQueries({ queryKey: nk(project, 'flow') })
      setTouched(false)
    } catch (e) {
      setError({ copy: sceneErrorCopy(e), reload: sceneErrorWantsReload(e) })
    } finally {
      setBusy(false)
    }
  }
  const confirm = () => {
    setTouched(true)
    const e = anchorInputErrors(lat, lng)
    if (e.lat || e.lng) return
    void write({ lat, lng, source: 'manual', confirmed: true })
  }
  const reload = () => { setTouched(false); setError(null); void q.refetch() }

  return (
    <div ref={ref} data-testid="site-anchor-field" className="flex flex-col gap-2">
      <div className="text-[11px] text-muted">
        One point for the whole project, used to place a new 3D site. Moving it never moves a site already created.
      </div>
      <div className="flex flex-wrap items-start gap-3">
        <NumberField label="Latitude" unit="°" value={lat} testId="site-anchor-lat" min={-90} max={90}
          disabled={!!blocked || busy} error={errs.lat}
          onChange={v => { setTouched(true); setLat(v) }} />
        <NumberField label="Longitude" unit="°" value={lng} testId="site-anchor-lng" min={-180} max={180}
          disabled={!!blocked || busy} error={errs.lng}
          onChange={v => { setTouched(true); setLng(v) }} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" data-testid="site-anchor-confirm" onClick={confirm}
          disabled={!!blocked || busy || !q.data || confirmedSame}
          className="px-2.5 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">
          Confirm coordinates
        </button>
        {server && (
          <button type="button" data-testid="site-anchor-clear" onClick={() => void write(null)}
            disabled={!!blocked || busy}
            className="px-2 py-1 rounded border border-border text-text hover:border-accent hover:text-accent disabled:opacity-50">
            Clear
          </button>
        )}
        <span data-testid="site-anchor-state" className="text-muted">
          {q.isError ? 'Site coordinates could not be read: reload to try again' : anchorSummary(q.data)}
        </span>
      </div>
      {blocked && <div data-testid="site-anchor-blocked" className="text-[11px] text-muted">{blocked}</div>}
      {error && (
        <div role="alert" data-testid="site-anchor-error" className="flex items-center gap-2 text-[11px] text-danger">
          <span>{error.copy}</span>
          {error.reload && (
            <button type="button" onClick={reload} className="underline text-text hover:text-accent">Reload</button>
          )}
        </div>
      )}
    </div>
  )
}
