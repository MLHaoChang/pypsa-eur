// Step 2 — Site (guided-mode spec §5.3): what the study needs to know about
// the site, in words, read from GET /results/eh_readiness. Every gap has a fix
// button that hands a §5.7 request to the assistant; the one choice is the
// site type (the archetype pack).
//
// Off-grid (P24-BE gate N4): the pack islands the import, so a tagged tie is
// "normally open", not a grid connection with a rating, and its missing
// outage data is not a gap — the island is studied without it.
import type { EhArchetype, EhReadiness } from '../../../api/simulation'
import { useHubDesignStore } from '../hubDesignStore'
import { siteFixText, type SiteFix } from '../delegate'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { Term, type TermKey } from '../shared/Term'
import { useHubReadiness, useHubStudy, useHubTemplate } from '../useHubData'

export const SITE_TYPES: { id: EhArchetype; label: string }[] = [
  { id: 'strong_grid', label: 'Strong grid — import whenever needed' },
  { id: 'weak_flexible', label: 'Weak or limited grid connection' },
  { id: 'off_grid', label: 'No grid — the site runs as an island' },
]

const mw = (v: number) => `${Number(v.toPrecision(6))} MW`

interface Row { value: string; gap: boolean; fix?: string }

/** The four rows in words. Exported for the smoke's expectations. */
export function siteRows(r: EhReadiness, archetype: EhArchetype): Record<SiteFix, Row> {
  const island = archetype === 'off_grid'
  const links = r.import?.links ?? []
  const grid: Row = island
    ? { value: links.length
      ? `${links.join(', ')} — normally open; the study runs the site as an island`
      : 'none — the study runs the site as an island', gap: false }
    : links.length
      ? { value: `${links.join(', ')}${r.import_p_nom_mw != null ? ` (${mw(r.import_p_nom_mw)})` : ''}`, gap: false }
      : { value: 'none tagged', gap: true, fix: siteFixText('grid') }

  const crit = r.critical_buses ?? []
  const critical: Row = crit.length
    ? { value: crit.join(', '), gap: false }
    : { value: 'none tagged', gap: true, fix: siteFixText('critical') }

  const scr = r.scr?.status
  const strength: Row = scr === 'ok' ? { value: 'data present', gap: false }
    : scr === 'not_required' ? { value: 'not needed for this site type', gap: false }
      : { value: `data missing${r.scr?.note ? ` — ${r.scr.note}` : ''}`, gap: true,
        fix: siteFixText('strength') }

  const ou = r.outage_units
  const missing = (ou?.missing ?? []).filter(m =>
    !(island && m.class === 'Link' && links.includes(m.name)))
  const outage: Row = !ou ? { value: 'not reported', gap: false }
    : missing.length
      ? { value: `${ou.count} units — ${missing.length} without outage data`, gap: true,
        fix: siteFixText('outage', missing.length) }
      : { value: `${ou.count} units`, gap: false }

  return { grid, critical, strength, outage }
}

const ROW_META: { k: SiteFix; term: TermKey; label: string }[] = [
  { k: 'grid', term: 'grid_connection', label: 'Grid connection' },
  { k: 'critical', term: 'critical_load', label: 'Must stay on' },
  { k: 'strength', term: 'grid_strength', label: 'Grid strength' },
  { k: 'outage', term: 'outage_data', label: 'Outage data' },
]

function SiteRow({ k, term, label, row }: { k: SiteFix; term: TermKey; label: string; row: Row }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-[12px]">
      <span data-testid={`hub-site-${k}`} className="flex-1 min-w-[16rem]">
        <span className={`mr-1 ${row.gap ? 'text-warn' : 'text-success'}`}>{row.gap ? '!' : '✓'}</span>
        <Term k={term}>{label}</Term>: <span className="text-text">{row.value}</span>
      </span>
      {row.gap && row.fix && (
        <DelegateButton testId={`hub-site-fix-${k}`} text={row.fix} label="Fix with the assistant" />
      )}
    </li>
  )
}

export function SiteCard() {
  const { running, isPending: studyPending } = useHubStudy()
  const { template, isPending: templatePending } = useHubTemplate()
  const archetype = useHubDesignStore(s => s.archetype)
  const setArchetype = useHubDesignStore(s => s.setArchetype)
  const { readiness, isError } = useHubReadiness(template, running,
    !studyPending && !templatePending)
  const rows = readiness ? siteRows(readiness, archetype) : null

  return (
    <CardShell step="site" testId="hub-card-site" title="Site">
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-semibold text-text"><Term k="site_type">Site type</Term></span>
        <select data-testid="hub-site-type" value={archetype}
          disabled={running}
          onChange={e => setArchetype(e.target.value as EhArchetype)}
          className="max-w-sm rounded border border-border bg-bg px-2 py-1 text-[12px] text-text">
          {SITE_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </label>

      <div data-testid="hub-site-readiness" className="flex flex-col gap-2">
        {running ? (
          <p data-testid="hub-site-paused" className="text-[12px] text-muted">
            Readiness is paused while the study runs
          </p>
        ) : rows ? (
          <ul className="flex flex-col gap-2">
            {ROW_META.map(m => <SiteRow key={m.k} {...m} row={rows[m.k]} />)}
          </ul>
        ) : (
          <p className="text-[12px] text-muted">
            {isError ? 'The site check could not be read — ask the assistant for help.'
              : 'Checking the site…'}
          </p>
        )}
      </div>
    </CardShell>
  )
}
