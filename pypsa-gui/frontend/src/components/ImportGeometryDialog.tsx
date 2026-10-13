import { Dialog } from './Dialog'
import type { ImportReport } from '../api/mapLayout'

// The match report of a GeoJSON / KML import (plan M3), shown BEFORE anything
// is written: one row per matched route with how it matched, the sites a
// polygon would become, and the features nothing claimed. Apply is the only
// write, and it is the user's click. Presentational: no fetch, no store — the
// map owns both ends.
interface Props {
  report: ImportReport
  fileName: string
  applying: boolean
  onApply: () => void
  onClose: () => void
}

const HOW_LABEL: Record<ImportReport['routes'][number]['how'], string> = {
  name: 'by name',
  endpoints: 'by endpoints',
}

export default function ImportGeometryDialog({ report, fileName, applying, onApply, onClose }: Props) {
  const nothing = report.routes.length === 0 && report.sites.length === 0
  return (
    <Dialog open onClose={onClose} title="Import geometry" z={950} panelClassName="bg-bg rounded-lg shadow-2xl w-[36rem] max-w-[94vw] max-h-[80vh] flex flex-col overflow-hidden border border-border">
      <div className="px-4 py-3 border-b border-border">
        <div className="text-sm font-semibold text-text">Import geometry</div>
        <div className="text-[11px] text-muted truncate" title={fileName}>
          {fileName} · {report.routes.length} route{report.routes.length === 1 ? '' : 's'}, {report.sites.length} site{report.sites.length === 1 ? '' : 's'}, {report.unmatched.length} unmatched
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3 text-xs flex flex-col gap-4" data-testid="import-report">
        {report.routes.length > 0 && (
          <section>
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Routes</h3>
            <table className="w-full text-left">
              <thead className="text-[10px] text-muted">
                <tr><th className="py-0.5 pr-2 font-medium">Feature</th><th className="py-0.5 pr-2 font-medium">Branch</th><th className="py-0.5 pr-2 font-medium">How</th><th className="py-0.5 font-medium">Vertices</th></tr>
              </thead>
              <tbody>
                {report.routes.map(r => (
                  <tr key={`${r.feature}-${r.key}`} className="border-t border-border/60">
                    <td className="py-1 pr-2 truncate max-w-[12rem]" title={r.label}>{r.label}</td>
                    <td className="py-1 pr-2 font-mono text-text">{r.key}</td>
                    <td className="py-1 pr-2 text-muted" data-testid="how">{HOW_LABEL[r.how]}{r.reversed ? ' (reversed)' : ''}</td>
                    <td className="py-1 text-muted">{r.points.length === 0 ? 'chord' : `${r.points.length} bend${r.points.length === 1 ? '' : 's'}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
        {report.sites.length > 0 && (
          <section>
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Sites</h3>
            <ul className="flex flex-col gap-0.5">
              {report.sites.map(s => (
                <li key={s.id} className="flex items-baseline gap-2">
                  <span className="font-medium text-text">{s.name}</span>
                  <span className="text-muted">{s.boundary.length} corners · {s.buses.length === 0 ? 'no placed bus inside' : `buses: ${s.buses.join(', ')}`}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {report.unmatched.length > 0 && (
          <section>
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Unmatched</h3>
            <ul className="flex flex-col gap-0.5" data-testid="unmatched">
              {report.unmatched.map(u => (
                <li key={`${u.feature}-${u.reason}`} className="text-muted"><span className="text-text">{u.label}</span> — {u.reason}</li>
              ))}
            </ul>
          </section>
        )}
        {nothing && <p className="text-muted">Nothing in this file matched a branch or formed a site boundary. Name features after your Lines, Links or Transformers, or draw them bus to bus.</p>}
      </div>
      <div className="px-4 py-3 border-t border-border flex items-center justify-end gap-2">
        <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded border border-border text-text hover:bg-border/30">Cancel</button>
        <button
          type="button"
          onClick={onApply}
          disabled={nothing || applying}
          className="px-3 py-1.5 text-xs rounded bg-accent text-white hover:opacity-90 disabled:opacity-40"
        >
          {applying ? 'Applying…' : `Apply ${report.routes.length + report.sites.length} change${report.routes.length + report.sites.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </Dialog>
  )
}
