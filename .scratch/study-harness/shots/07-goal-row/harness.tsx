// Shot harness for #1257 (SH-07 B3b PR 2): the plan-review card's goal row,
// beside the view its "Missing: Reliability goal (LOLE) ›" link opens. Real
// components; Playwright mocks the API routes (shoot.mjs). shoot.mjs copies
// this file to src/goal-row.tmp.tsx and removes it in a finally block.
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import { useChatStore } from './store/chatStore'
import { ChoiceCard } from './components/ChatPanel'
import { FlowReturnButton } from './layout/FlowReturnButton'
import { GoalCard } from './pages/hubDesign/cards/GoalCard'
import SolverSettings from './pages/SolverSettings'
import { ENVELOPE, TASK_ID } from './components/runMonitor/runFixtures'

const q = new URLSearchParams(location.search)
const mode = (q.get('mode') ?? 'guided') as 'guided' | 'expert'
const goal = JSON.parse(q.get('goal') ?? 'null')
const envelope = { ...ENVELOPE, goal }
useUIStore.setState({ uiMode: mode, currentProject: 'Demo', activeSection: 'project', activeSlidePanel: null } as never)
useChatStore.setState({
  choice: { tool_use_id: 'tu_9', intent: 'plan_review', title: 'Own site study', question: 'Approve this plan?',
    detail: '1. Brief\n2. Set up', options: [{ label: 'Approve' }, { label: 'Change' }], allow_free_text: true },
  messages: [{ id: 'm8', ts: 0, role: 'tool', content: '', tool_use_id: 'tu_8', tool_name: 'start_task',
    tool_result: { task_id: TASK_ID, title: 'Own site study', status: 'ready', run: { stage: 'intake' }, envelope } }],
} as never)

function Main() {
  const ui = useUIStore()
  const opened = ui.activeSection === 'scenarios'
  return <div className="flex-1 min-w-0 overflow-auto p-3" data-testid="shot-main">
    <FlowReturnButton />
    {!opened && <div className="text-muted text-[12px]">Project &amp; site (the view the user was on)</div>}
    {opened && (ui.uiMode === 'guided' ? <GoalCard /> : <SolverSettings />)}
  </div>
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}>
    <div className="flex h-screen bg-bg text-text">
      <Main />
      <div className="w-[380px] shrink-0 border-l border-border overflow-auto p-2"><ChoiceCard /></div>
    </div>
  </QueryClientProvider>)
