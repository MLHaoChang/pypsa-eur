// Screenshot harness for PR #727 (g2p 17a, flicker screen): renders the real Link edit card (PropertiesPanel's
// LinkCard, the component Expert's properties panel and Guided's field drawer mount), the real grid-readiness
// scorecard and the real grid-code section. API routes are mocked by Playwright with the PR backend's own output
// (data.json). The scorecard's "Go to field" (Expert) opens the target unit's card, as the app's jump opens its
// component.
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { LinkCard } from './layout/PropertiesPanel'
import { GridReadinessScorecard } from './components/gridReadiness/GridReadinessScorecard'
import CampusGridCodeSection from './pages/CampusGridCodeSection'
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
  const [jumped, setJumped] = useState<string | null>(null)
  const { data } = useQuery({ queryKey: ['harness', which], queryFn: () => fetch(`/api/__harness/${which}`).then(r => r.json()) })
  if (jumped) return <LinkView name={jumped} />
  if (!data) return <p>Loading…</p>
  return (
    <div data-testid="harness-score" className="m-4 max-w-3xl border border-border rounded-lg bg-panel">
      <GridReadinessScorecard data={data as GridReadiness} mode={mode} onJump={(_t, name) => setJumped(name)} />
    </div>
  )
}

const view = q.get('view')
const el = view === 'score' ? <ScoreView which={q.get('case') ?? 'gr_warn'} />
  : view === 'codes' ? <div data-testid="harness-codes" className="m-4 "><CampusGridCodeSection name="Demo" onProfilesChanged={() => undefined} /></div>
  : <LinkView name={q.get('name') ?? 'conveyor'} />
createRoot(document.getElementById('root')!).render(
  <MemoryRouter initialEntries={['/harness']}>
    <QueryClientProvider client={client}><div className="bg-bg min-h-screen">{el}</div><ToasterHost /></QueryClientProvider>
  </MemoryRouter>)
