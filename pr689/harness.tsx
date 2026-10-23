// Screenshot harness for PR #689: renders the real grid-code section; API routes are mocked by Playwright.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import CampusGridCodeSection from './pages/CampusGridCodeSection'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}>
    <div className="p-4 max-w-4xl"><CampusGridCodeSection name="Demo" onProfilesChanged={() => undefined} /></div>
  </QueryClientProvider>)
