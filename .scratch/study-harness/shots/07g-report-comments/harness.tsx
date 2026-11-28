// Shot harness for SH-07g (report comments in the report viewer). Real
// ReportViewer; Playwright mocks the API routes (shoot.mjs). shoot.mjs copies
// this file to src/report-comments.tmp.tsx and removes it in a finally block.
// No AuthProvider: myEmail is null, so comments with created_by null are "mine".
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { ReportViewer } from './pages/reports/ReportViewer'

const meta = {
  report_id: 'r1', title: 'Own site study — report', created_at: '2026-11-26T10:00:00+00:00',
  updated_at: '2026-11-28T09:00:00+00:00', latest_version: 3, mode: 'generated' as const, evidence_hash: 'e',
}
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}>
    <div className="p-4 max-w-[960px]" data-testid="shot-main">
      <ReportViewer project="Demo" meta={meta} onBack={() => {}} />
    </div>
  </QueryClientProvider>,
)
