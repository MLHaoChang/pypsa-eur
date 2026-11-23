// Hand-off › Specialist packet (card-workspaces.md §3.6, R6): what a dynamics specialist
// receives. On a planning → dynamics project: the dispatch source, the ranked snapshots with
// their per-hour bundles, and the assumptions ledger, read from the study (the same reads as
// the Planning → dynamics panel, which keeps running and editing the study). Other projects
// have no packet yet; the tab says what it will hold.
import { useQuery } from '@tanstack/react-query'
import { gridspineApi, isNotAStudy } from '../../api/gridspine'
import { useUIStore } from '../../store/uiStore'
import { PageBody, PageSection } from '../../components/PageKit'
import {
  CONFIG_KEY, LEDGER_KEY, SNAPSHOTS_KEY, STATUS_KEY, LedgerSection, SnapshotTable, describeSource,
} from '../GridspinePanel'

export default function SpecialistPacketTab() {
  const name = useUIStore(s => s.currentProject)
  const status = useQuery({
    queryKey: STATUS_KEY(name ?? ''), queryFn: () => gridspineApi.status(name!),
    enabled: !!name, retry: false,
  })
  const isStudy = status.isSuccess
  const done = status.data?.status === 'completed'
  const snapshots = useQuery({
    queryKey: SNAPSHOTS_KEY(name ?? ''), queryFn: () => gridspineApi.snapshots(name!),
    enabled: !!name && done, retry: false,
  })
  const ledger = useQuery({
    queryKey: LEDGER_KEY(name ?? ''), queryFn: () => gridspineApi.ledger(name!),
    enabled: !!name && isStudy, retry: false,
  })
  const config = useQuery({
    queryKey: CONFIG_KEY(name ?? ''), queryFn: () => gridspineApi.config(name!),
    enabled: !!name && isStudy, retry: false,
  })

  if (!name || status.isLoading) {
    return <PageBody><p className="text-[12px] text-muted" data-testid="packet-loading">{name ? 'Loading…' : 'Open a project to see its specialist packet.'}</p></PageBody>
  }
  if (!isStudy) {
    const unknown = status.isError && !isNotAStudy(status.error)
    return (
      <PageBody>
        <PageSection title="Specialist packet">
          <p data-testid="packet-empty" className="text-[12px] text-muted">
            {unknown
              ? 'The packet could not be read. Try again in a moment.'
              : 'A planning → dynamics project hands a dynamics specialist the dispatch source, the ranked snapshots, the assumptions ledger and one bundle per hour (.raw, .dyr, contingencies). A project with a campus Grid study will hand over its PSS/E bundles here once that export lands. This project has neither yet.'}
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return (
    <PageBody>
      {config.data && (
        <PageSection title="Dispatch source">
          <p className="text-[12px]" data-testid="packet-source">Dispatch from {describeSource(config.data)}.</p>
        </PageSection>
      )}
      <PageSection title="Ranked snapshots" count={done ? snapshots.data?.length : undefined}
        hint="The k most extreme hours under each criterion, with each hour's bundle">
        {!done ? (
          <p className="text-[12px] text-muted" data-testid="packet-not-run">
            The study has not run yet, so there are no hours or bundles. Run it in Planning → dynamics.
          </p>
        ) : snapshots.data ? (
          <SnapshotTable rows={snapshots.data} name={name} bundles={status.data?.bundles ?? {}} />
        ) : (
          <p className="text-[12px] text-muted">{snapshots.isError ? 'The ranked snapshots could not be read.' : 'Loading…'}</p>
        )}
      </PageSection>
      {ledger.data && <LedgerSection ledger={ledger.data} />}
    </PageBody>
  )
}
