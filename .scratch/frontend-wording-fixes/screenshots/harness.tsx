// Screenshot harness for PR #653: renders real components; API routes are mocked by Playwright.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { useChatStore } from './store/chatStore'
import { IslandReportView } from './pages/CampusIslandSection'
import { IslandTab } from './pages/results/ControlStudyTabs'
import { ConfirmationCard } from './components/ConfirmationCard'
import Results from './pages/Results'

const q = new URLSearchParams(location.search)
const view = q.get('view')
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo', compareRailOpen: false, activeSlidePanel: 'results' } as never)

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const REPORT = { caveat: 'Screening, not a certificate.', missing: [], tables: [
  { id: 'requirements', group: 'inputs', title: 'Island requirements', model: 'island sidecar', caveat: 'Screening, not a certificate.',
    columns: ['field', 'value', 'source'],
    rows: [['bridge_s', 600, 'assumed'], ['sustained_h', 24, 'datasheet'], ['steady_island.f_max_hz', 51, 'datasheet']], assumed_rows: [0] }] }

let el: JSX.Element
if (view === 'report') {
  el = <div className="p-4 max-w-3xl"><IslandReportView report={REPORT as never} /></div>
} else if (view === 'control') {
  el = <div style={{ height: '100vh' }}><IslandTab /></div>
} else if (view === 'edit') {
  const typed = { component_class: 'Store', name: 'H2 tank', field: 'e_nom', label: 'Energy capacity', old: 123456789012.3456,
    new: 987654321098.7654, unit: 'MWh', role: 'typed' }
  useChatStore.setState({ sessionId: 's', pending: { tool_use_id: 'c', tool_name: 'update_component',
    args: { component_class: 'Store', name: 'H2 tank', attrs: { e_nom: 987654321098.7654 } }, safety_tier: 'write',
    confirmation_token: 't', ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300000,
    edit_card: { version: 1, kind: 'parameter_edit', tool: 'update_component', complete: true,
      says: 'H2 tank e_nom', digest: 'd'.repeat(64), rows: [typed] } } } as never)
  useUIStore.setState({ assistantDockOpen: true } as never)
  el = <div className="p-4 max-w-xl"><ConfirmationCard /></div>
} else {
  const s = q.get('start')!, e = q.get('end')!
  useUIStore.getState().requestResultsWindow({ start: s, end: e })
  useUIStore.getState().requestResultsTab('summary' as never)
  el = <div style={{ height: '100vh' }}><Results /></div>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
