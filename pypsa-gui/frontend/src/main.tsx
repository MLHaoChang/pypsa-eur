import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Toaster } from 'react-hot-toast'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthModeProvider } from './auth/AuthModeProvider'
import { AuthProvider } from './auth/AuthProvider'
import AppRoutes from './routes'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    // refetchOnWindowFocus OFF: the app drives its own refetch intervals
    // (status, queue, changelog, meta), so re-fetching every query on every
    // Alt-Tab back to the browser was a pure refetch storm + re-render jank
    // with no freshness benefit.
    queries: { retry: 1, staleTime: 5000, refetchOnWindowFocus: false },
    mutations: { retry: 0 },
  },
})

declare global {
  interface Window {
    __PYPSA_AUTH_BOOT__?: Promise<string>
  }
}

async function boot(): Promise<void> {
  // Wait for the pre-React gate in index.html. If it already redirected away,
  // this promise may never settle in this document — which is fine.
  if (window.__PYPSA_AUTH_BOOT__) {
    await window.__PYPSA_AUTH_BOOT__
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <BrowserRouter>
        <QueryClientProvider client={queryClient}>
          <AuthModeProvider>
            <AuthProvider>
              <AppRoutes />
            </AuthProvider>
          </AuthModeProvider>
          <Toaster
            position="bottom-right"
            toastOptions={{
              style: {
                fontSize: 13,
                background: 'var(--color-panel)',
                color: 'var(--color-text)',
                border: '1px solid var(--color-border)',
              },
            }}
          />
        </QueryClientProvider>
      </BrowserRouter>
    </StrictMode>,
  )
}

void boot()

