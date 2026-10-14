// The Site connection section (IC U1 follow-up b), moved out of InvestmentTab.tsx
// unchanged so Expert → Economics can mount it too (Economics in Expert, E0).
import { useState } from 'react'
import type { CommercialConfig } from '../../../api/types'
import SiteConnectionForm from '../SiteConnectionForm'

/** The commercial root (IC U1 follow-up b): the form when it is missing (or a
 *  result says the config is), else a summary line that opens it. */
export default function SiteConnectionSection({ config, needed }: { config: CommercialConfig | null | undefined
                                                     needed: boolean }) {
  const [editing, setEditing] = useState(false)
  const root = config?.poc_link
    ? { poc_link: config.poc_link, export_link: config.export_link ?? null,
        timezone: config.timezone ?? null }
    : null
  if (needed || !root) {
    return (
      <section className="space-y-2 border border-border rounded p-2" data-testid="ic-site-connection"
               aria-labelledby="ic-site-connection-title">
        <h3 id="ic-site-connection-title" className="text-[12px] font-semibold">Site connection</h3>
        <p className="text-[11px] text-muted">
          Set the site's connection to the grid first: the bill, the participants and the
          investment case are all read at the point of connection.</p>
        <SiteConnectionForm initial={root} />
      </section>
    )
  }
  return (
    <div className="space-y-2" data-testid="ic-site-connection">
      <p className="text-[11px] text-muted" data-testid="ic-site-connection-summary">
        Site connection: imports through {root.poc_link}
        {root.export_link ? `, exports through ${root.export_link}` : ', no export link'}
        {root.timezone ? `, site time ${root.timezone}` : ', snapshots in site time'}.{' '}
        <button type="button" className="underline" aria-expanded={editing}
                onClick={() => setEditing(e => !e)}>Edit site connection</button>
      </p>
      {editing && <SiteConnectionForm initial={root} onSaved={() => setEditing(false)}
                                      onCancel={() => setEditing(false)} />}
    </div>
  )
}
