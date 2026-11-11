// Screenshot harness for the RP-05/RP-07 UX review: mounts TimeSeriesManager with
// every /api call route-mocked by shots.mjs. Copied into pypsa-gui/frontend/ at run time.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import TimeSeriesManager from './src/pages/TimeSeriesManager'
import { useUIStore } from './src/store/uiStore'
import './src/index.css'

const mode = new URLSearchParams(location.search).get('mode') === 'guided' ? 'guided' : 'expert'
useUIStore.setState({ currentProject: 'Site A', uiMode: mode })
createRoot(document.getElementById('root')!).render(
  <BrowserRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <div style={{ height: '100vh' }}><TimeSeriesManager /></div>
  </QueryClientProvider></BrowserRouter>)
