// GC-09 item 6 component harness: the real CampusPowerFactorySection (through the real campus API client), with
// the app's CSS and theme store. Every /api request is answered by Playwright (shoot.mjs).
//   /gc09pf-harness.html?theme=light|dark
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './src/index.css'
import { useUIStore } from './src/store/uiStore'
import { CampusPowerFactorySection } from './src/pages/CampusElectricalPanel'

const theme = (new URLSearchParams(location.search).get('theme') ?? 'light') as 'light' | 'dark'
const PROJECT = 'Harbour Hub'
const ui = useUIStore.getState()
ui.setTheme(theme)
document.documentElement.dataset.theme = theme  // what App's effect does
ui.setUiMode('expert', { explicit: true })
useUIStore.setState({ currentProject: PROJECT })

const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <div className="bg-bg text-text min-h-screen p-4">
        <div className="max-w-[960px] rounded-lg border border-border bg-bg p-2" data-testid="harness">
          <CampusPowerFactorySection name={PROJECT} />
        </div>
      </div>
    </QueryClientProvider>
  </StrictMode>,
)
