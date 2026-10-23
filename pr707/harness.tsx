// Screenshot harness for PR #707 (g2p 17a, harmonic screen): renders the real Link edit card (PropertiesPanel's
// LinkCard, the same component Expert's properties panel and Guided's field drawer mount) and the real
// grid-readiness scorecard. API routes are mocked by Playwright with output of the PR's own backend (data.json).
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { LinkCard } from './layout/PropertiesPanel'
import { GridReadinessScorecard } from './components/gridReadiness/GridReadinessScorecard'
import ToasterHost from './components/ToasterHost'
import type { Link } from './api/types'
import { networkApi } from './api/network'
import { nk } from './utils/queryKeys'
import type { GridReadiness } from './api/campusElectrical'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

function LinkView({ name }: { name: string }) {
  const { data } = useQuery({ queryKey: nk('Demo', 'links'), queryFn: networkApi.getLinks })
  const link = (data as Link[] | undefined)?.find(l => l.name === name)
  if (!link) return <p>Loading…</p>
  return (
    <aside data-testid="harness-panel" className="bg-bg border-r border-border p-2" style={{ width: 300 }}>
      <LinkCard link={link} mode="detail" />
    </aside>
  )
}

function ScoreView({ which }: { which: string }) {
  const { data } = useQuery({ queryKey: ['harness', which], queryFn: () => fetch(`/api/__harness/${which}`).then(r => r.json()) })
  if (!data) return <p>Loading…</p>
  return (
    <div data-testid="harness-score" className="m-4 max-w-3xl border border-border rounded-lg bg-panel">
      <GridReadinessScorecard data={data as GridReadiness} mode={mode} />
    </div>
  )
}

const el = q.get('view') === 'score' ? <ScoreView which={q.get('case') ?? 'gr_6pulse'} />
  : <LinkView name={q.get('name') ?? 'conveyor'} />
createRoot(document.getElementById('root')!).render(
  <MemoryRouter initialEntries={['/harness']}>
    <QueryClientProvider client={client}><div className="bg-bg min-h-screen">{el}</div><ToasterHost /></QueryClientProvider>
  </MemoryRouter>)
