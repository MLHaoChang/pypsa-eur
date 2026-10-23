// Screenshot harness for PR #671: renders the real investment case view and island report; API routes are mocked by Playwright.
import { createRoot } from 'react-dom/client'
import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import InvestmentCaseView from './pages/results/investment/InvestmentCaseView'
import CampusElectricalPanel from './pages/CampusElectricalPanel'
import { IslandReportView } from './pages/CampusIslandSection'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo' } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

function Island() {
  const [r, setR] = useState<never>()
  useEffect(() => { fetch('/api/__golden').then(x => x.json()).then(setR) }, [])
  return <IslandReportView report={r} />
}
const view = q.get('view')
const el = view === 'island' ? <div className="p-4 max-w-4xl"><Island /></div>
  : view === 'library' ? <div className="p-4 max-w-4xl"><CampusElectricalPanel /></div>
  : <div className="p-4 max-w-4xl"><InvestmentCaseView /></div>
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
