// Screenshot harness for PR #665: renders the real campus panels; API routes are mocked by Playwright.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import CampusElectricalPanel, { CampusResultsTab } from './pages/CampusElectricalPanel'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const el = q.get('view') === 'library'
  ? <div className="p-4 max-w-4xl"><CampusElectricalPanel /></div>
  : <div className="p-4 max-w-4xl"><CampusResultsTab name="Demo" part="electrical_design" /></div>
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
