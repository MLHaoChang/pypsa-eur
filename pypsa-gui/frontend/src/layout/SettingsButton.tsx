import { SlidersHorizontal } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { useLocalSettingsAvailable } from '../hooks/useLocalSettings'
import { useLLMSettingsAvailable } from '../hooks/useLLMSettings'

// App settings live top-right, beside the account, not in the SIMULATION
// group of the sidebar: they configure the application (assistant model, the
// desktop API key and log), not the model being solved (UX assessment Q10).
// A header button rather than a user-menu entry, because the desktop app has
// no user menu and is exactly where the API key matters.
//
// The Settings pane hosts two independently-gated surfaces (Task 15): the
// desktop-only Anthropic key + log (routes 404 on a web deployment) and
// AssistantModelSettings (super-admin-gated, meaningful on a server too).
// The button shows when EITHER is reachable — hiding on local-settings alone
// would take it away from a web super-admin who can still reach the
// assistant-model section. A control that opens a genuinely empty panel is
// still worse than no control, which is why this stays an OR rather than
// always-on. Both hooks share their pane's own react-query fetch.
export default function SettingsButton() {
  const activeSlidePanel = useUIStore(s => s.activeSlidePanel)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const localSettingsAvailable = useLocalSettingsAvailable()
  const llmSettingsAvailable = useLLMSettingsAvailable()
  if (!(localSettingsAvailable || llmSettingsAvailable)) return null
  const open = activeSlidePanel === 'settings'
  return (
    <button
      type="button"
      onClick={() => setSlidePanel(open ? null : 'settings')}
      aria-label="Settings"
      aria-pressed={open}
      title="Settings: the assistant model and, on desktop, your Anthropic API key and the application log."
      data-testid="header-settings"
      className={`p-1.5 rounded border transition-colors ${open
        ? 'text-accent border-accent/40 bg-accent/5'
        : 'text-muted border-transparent hover:text-text hover:border-border'}`}
    >
      <SlidersHorizontal size={14} />
    </button>
  )
}
