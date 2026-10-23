// Screenshot harness for PR #691: renders the real grid-code section, campus results and hub chapter tiles;
// API routes are mocked by Playwright with output of the PR's own backend services (generic SAGC profile).
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import CampusGridCodeSection from './pages/CampusGridCodeSection'
import { CampusResultsPanel } from './pages/CampusElectricalPanel'
import { ChaptersSection } from './pages/hubDesign/ChaptersSection'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const view = q.get('view')
const el = view === 'codes' ? <div className="p-4 max-w-5xl"><CampusGridCodeSection name="Demo" onProfilesChanged={() => undefined} /></div>
  : view === 'chapters' ? <div className="p-4 max-w-2xl"><ChaptersSection reliability={{ hasResults: false, running: false,
      lole: null, target: null, verdict: null }} /></div>
  : <div className="p-4 max-w-6xl"><CampusResultsPanel /></div>
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
