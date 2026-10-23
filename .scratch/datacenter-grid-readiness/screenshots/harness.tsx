// Screenshot harness for PR #659: real components; API routes mocked by Playwright.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { ConnectionLimits } from './components/gridReadiness/ConnectionLimits'
import PropertiesPanel from './layout/PropertiesPanel'
import { FieldDrawer } from './components/FieldDrawer'

const q = new URLSearchParams(location.search)
const view = q.get('view')
const mode = (q.get('mode') ?? 'expert') as 'expert' | 'guided'
useUIStore.setState({ uiMode: mode, currentProject: 'Demo', compareRailOpen: false } as never)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

let el: JSX.Element
if (view === 'limits') {
  el = <div className="p-4 max-w-xl"><ConnectionLimits /></div>
} else if (view === 'props') {
  useUIStore.setState({ selectedComponent: { type: 'Bus', name: q.get('bus') ?? 'grid' } } as never)
  el = <div style={{ width: 360 }}><PropertiesPanel /></div>
} else {
  // drawer: bus Properties beside the Guided field drawer, as App.tsx mounts them
  useUIStore.setState({ selectedComponent: { type: 'Bus', name: 'grid' } } as never)
  el = <div style={{ display: 'flex', height: '100vh' }}>
    <div style={{ width: 360 }}><PropertiesPanel /></div>
    <FieldDrawer>{() => <div className="px-3"><ConnectionLimits only={['scr']} /></div>}</FieldDrawer>
  </div>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
