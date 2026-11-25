// GC-08e component harness: the real GridReadinessScorecard (through the real useGridReadiness and campus API
// client) and the real report SectionCard (through the real getReport), with the app's CSS and theme store.
// Every /api request is answered by Playwright (shoot.mjs) from data.json, the backend's own output.
//   /gc08e-harness.html?state=S1|S2|S5|S6|P1|P2&mode=guided|expert&theme=light|dark
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import './src/index.css'
import { useUIStore } from './src/store/uiStore'
import { GridReadinessScorecard, useGridReadiness, type ScorecardMode } from './src/components/gridReadiness/GridReadinessScorecard'
import { SectionCard } from './src/pages/reports/SectionCard'
import { getReport } from './src/api/reports'

const q = new URLSearchParams(location.search)
const state = q.get('state') ?? 'S1'
const mode = (q.get('mode') ?? 'guided') as ScorecardMode
const theme = (q.get('theme') ?? 'light') as 'light' | 'dark'
const PROJECT = 'Harbour Hub'

const ui = useUIStore.getState()
ui.setTheme(theme)
ui.setUiMode(mode, { explicit: true })
useUIStore.setState({ currentProject: PROJECT })

function Scorecard() {
  const { data } = useGridReadiness()
  if (!data) return <div data-testid="loading">loading</div>
  return <GridReadinessScorecard data={data} mode={mode} />
}

function Pack() {
  const { data: doc } = useQuery({ queryKey: ['report', state], queryFn: () => getReport(PROJECT, `gc08e-${state}`) })
  if (!doc) return <div data-testid="loading">loading</div>
  return <SectionCard section={doc.sections[0]} doc={doc} project={PROJECT} />
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <div className="bg-bg text-text min-h-screen p-4">
        <div className="max-w-[960px] rounded-lg border border-border bg-bg" data-testid="harness">
          {state.startsWith('P') ? <Pack /> : <Scorecard />}
        </div>
      </div>
    </QueryClientProvider>
  </StrictMode>,
)
