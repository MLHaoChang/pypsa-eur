/**
 * Phase 3 chatbot integration v6 — ChatPanel.
 *
 * UI shell for the chat assistant. Mounted unconditionally inside
 * `AssistantDock` (its own column beside the main area, hidden with CSS when
 * collapsed) — NOT in the SlidePanel slot it used to occupy as kind='chat'.
 * That move is the fix for "it switches to the results panel, but the chat
 * disappears": `activeSlidePanel` holds one value, so as a slide panel the
 * assistant was mutually exclusive with every view it exists to explain. The
 * panel owns:
 *   * message list (assistant token deltas accumulate into one assistant
 *     bubble until a tool_request / tool_result lands)
 *   * confirmation card (renders when chatStore.pending is set; carries a
 *     live countdown and approve/deny buttons that POST /confirm)
 *   * usage meter (exact token counts from session usage_acc — no currency
 *     estimate; see the note in chatStore.ts for why that was removed)
 *   * project_exists / descendants_exist UX paths (v4-MAJOR-1 / v4-MINOR-1)
 *   * connection-lost toast (M8)
 *
 * Cleanup discipline (CLAUDE.md SSE pattern):
 *   * Every useEffect that opens a stream returns a cleanup that calls the
 *     stream's cancel function.
 *   * Confirmation card countdown timer is tracked via a ref so a re-render
 *     never leaks a setInterval.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'

import {
  createChatStream,
  getChatHistory,
  postChatAbort,
  postChatConfirm,
  type ChatFrame,
  type InterruptedTurn,
} from '../api/chat'
import { useChatProfiles, CHAT_PROFILES_QUERY_KEY } from '../hooks/useChatProfiles'
import { nk } from '../utils/queryKeys'
import { invalidateAssetQueries, isMutatingTier } from '../utils/assetWrite'
import {
  deleteUpload,
  getUploadBlobUrl,
  listUploads,
  uploadFile,
  UploadError,
  type UploadMeta,
} from '../api/uploads'
import { useChatStore, type ChatMessage, type UploadMetaUI } from '../store/chatStore'
import ApiKeySetup from './ApiKeySetup'
import ChatLaunchGreeting from './ChatLaunchGreeting'
import { buildUiContext } from '../utils/uiContext'
import { postChatRewind } from '../api/chat'
import * as speechOut from '../utils/speechOut'
import { useUIStore } from '../store/uiStore'
import { useIsCoarsePointer } from '../hooks/useIsCoarsePointer'
import { useSpeechToText } from '../hooks/useSpeechToText'
import { isNearBottom } from '../utils/chatUi'
import { insertAtCursor } from '../utils/speechToText'
import ChatMarkdown from './ChatMarkdown'
import { UploadProgressToast } from './UploadProgressToast'

// Phase D — extensions accepted by the file picker / drag-drop overlay.
// Matches the upload_service.ALLOWED_MIME_TYPES allowlist.
const UPLOAD_ACCEPT = '.xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp,.gif,.docx'
const UPLOAD_MAX_BYTES = 25 * 1024 * 1024

// F2 — every localStorage touch in this panel is a UI PREFERENCE: a dismissed
// touch hint, a drag-resized prompt height, and two opt-in flags. Losing any of
// it costs the user nothing they would notice; taking the panel down for it
// costs them the whole assistant.
//
// Both failure modes are real and neither is exotic. Reading the
// `window.localStorage` PROPERTY throws `SecurityError` outright when the
// browser is configured to block site data — the throw is on the property
// access, before any method is called, which is why the guard has to wrap the
// access and not just the call. `setItem` separately throws
// `QuotaExceededError` in Safari Private Browsing and when the origin's quota
// is full.
//
// Two of the four reads run inside `useState` initializers, so an unguarded
// throw happens DURING render: React unwinds the whole subtree and the user
// gets a blank chat pane rather than a forgotten preference. That is the defect
// being fixed here, not the lost setting.
//
// The rest of the codebase (topologyLayoutStore.ts, selectionMemory.ts and ~7
// others) already inlines try/catch at each site. These two helpers are the
// same discipline factored out because this one file has ten sites.
function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Private mode, blocked site data, or a full quota. The panel keeps
    // working; it just forgets this preference on the next load.
  }
}

function _frame_data<T = Record<string, unknown>>(f: ChatFrame): T {
  return f.data as T
}

/**
 * Map chat tool panel_id → SlidePanel / special navigation targets.
 *
 * Not every value here is a `SlidePanel`. 'topology', 'map', 'properties',
 * 'palette', 'bottom', 'import_export', 'project_picker', 'new_project' and
 * 'chat' name surfaces that live outside `activeSlidePanel`; applyUiNavigate
 * dispatches on each of them explicitly before falling through to the
 * setSlidePanel branch. 'chat' in particular now resolves to the assistant
 * dock, not to a slide panel.
 */
function _normalizePanelId(raw: string): string {
  const key = raw.trim()
  const aliases: Record<string, string> = {
    Results: 'results', results: 'results',
    SolverSettings: 'simparams', simparams: 'simparams',
    TimeSeriesManager: 'timeseries', LoadProfileManager: 'timeseries', timeseries: 'timeseries',
    VintagePeriodBoundsModal: 'capacityBounds', capacityBounds: 'capacityBounds',
    OverviewPanel: 'overview', overview: 'overview',
    IssuesPanel: 'issues', issues: 'issues',
    Scenarios: 'scenarios', scenarios: 'scenarios',
    Snapshots: 'snapshots', snapshots: 'snapshots',
    Horizon: 'horizon', horizon: 'horizon',
    SolveQueue: 'solveQueue', solveQueue: 'solveQueue',
    Chat: 'chat', chat: 'chat',
    Compare: 'compare', compare: 'compare',
    Topology: 'topology', topology: 'topology',
    MapCanvas: 'map', map: 'map',
    PropertiesPanel: 'properties', properties: 'properties',
    BottomPanel: 'bottom',
    CommandPalette: 'palette', palette: 'palette',
    ImportExport: 'import_export', import_export: 'import_export',
    GenerationStack: 'results',
    ProjectPicker: 'project_picker', project_picker: 'project_picker',
    OpenProject: 'project_picker',
    NewProject: 'new_project', new_project: 'new_project',
    NewProjectWizard: 'new_project',
  }
  return aliases[key] ?? key
}

function applyUiNavigate(d: {
  kind?: string
  panel_id?: string
  results_tab?: string
  bottom_tab?: string
  compare_rail?: boolean
  compare_a?: string
  compare_b?: string
  compare_tab?: string
  component_class?: string
  name?: string
  snapshot_iso?: string
  period?: number | null
  category?: string
  metrics?: string[]
  mode?: 'chronological' | 'duration' | 'monthly'
  chart?: boolean
}) {
  const ui = useUIStore.getState()
  if (d.kind === 'select_component' && d.component_class && d.name) {
    ui.setSelectedComponent({ type: d.component_class, name: d.name })
    ui.openRightPanel()
    return
  }
  if (d.kind === 'open_asset_detail' && d.component_class && d.name) {
    ui.requestAssetDetail({
      componentClass: d.component_class,
      name: d.name,
      category: d.category,
      metrics: d.metrics,
      mode: d.mode,
      chart: d.chart,
    })
    return
  }
  if (d.kind === 'set_snapshot' && d.snapshot_iso) {
    // Snapshot picker is driven by ISO + optional period; store the index
    // is unknown here — open Results so the user sees the picker context.
    ui.setSlidePanel('results')
    return
  }
  // kind === 'navigate' | legacy 'open_panel'
  const panel = d.panel_id ? _normalizePanelId(d.panel_id) : null
  if (panel === 'topology') {
    ui.setSlidePanel(null)
    ui.setCanvasView('blank')
  } else if (panel === 'map') {
    ui.setSlidePanel(null)
    ui.setCanvasView('satellite')
  } else if (panel === 'properties') {
    ui.openRightPanel()
  } else if (panel === 'palette') {
    ui.setPaletteMode('all')
  } else if (panel === 'import_export') {
    ui.requestIoModal('import')
  } else if (panel === 'project_picker') {
    window.dispatchEvent(new CustomEvent('chat:open-project-picker'))
  } else if (panel === 'new_project') {
    window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard'))
  } else if (panel === 'bottom') {
    // Expand bottom panel on a default tab if none specified below.
    if (!d.bottom_tab) ui.requestBottomTab('Buses')
  } else if (panel === 'compare') {
    ui.setSlidePanel('results')
    ui.setCompareRailOpen(true)
  } else if (panel === 'chat') {
    // 'chat' is no longer a SlidePanel member — it resolves to the dock. The
    // agent can still be asked to open the assistant, and doing so no longer
    // evicts whatever view is currently on screen.
    ui.setAssistantDockOpen(true)
  } else if (
    panel === 'results' || panel === 'simparams' || panel === 'timeseries'
    || panel === 'capacityBounds' || panel === 'overview' || panel === 'issues'
    || panel === 'scenarios' || panel === 'snapshots' || panel === 'horizon'
    || panel === 'solveQueue'
  ) {
    ui.setSlidePanel(panel)
  }

  if (d.results_tab) ui.requestResultsTab(d.results_tab)
  if (d.bottom_tab) ui.requestBottomTab(d.bottom_tab)
  if (typeof d.compare_rail === 'boolean') ui.setCompareRailOpen(d.compare_rail)
  if (d.compare_a || d.compare_b || d.compare_tab) {
    ui.requestCompareNav({
      a: d.compare_a,
      b: d.compare_b,
      tab: d.compare_tab,
    })
  }
}

interface SessionInitFrame {
  session_id: string
  model?: string
  // Task 13 — which profile actually resolved this turn. Display-only: the
  // store's `profileId` selector is never PINNED from this frame — it stays
  // `null` ("follow the server's active profile") unless the user picks one
  // explicitly, or a request sent while `profileId === null` would start
  // reasserting whatever the server last resolved instead of tracking future
  // admin changes / A8 fallbacks.
  profile_id?: string
  profile_label?: string
}

interface TokenFrame {
  delta: string
}

interface ModelFallbackFrame {
  from_model: string
  to_model: string
  reason: string
  profile_id?: string
}

interface ThinkingFrame {
  delta: string
}

interface ToolPendingFrame {
  tool_use_id: string
  tool_name: string
  args: Record<string, unknown>
  safety_tier: string
  confirmation_token: string
  ttl_seconds: number
}

// Tools that require an EXTRA typed-confirmation step (Phase 4 polish).
// v4-MAJOR-1 / v6-F1: save_project force-overwrite needs the user to type
// the EXISTING project name. v4-MINOR-1 + memory-rule: delete_project on
// the active project also requires typed confirmation so a misclick can't
// nuke the user's active work.
const TYPED_CONFIRMATION_TOOLS = new Set<string>([
  'delete_project', 'save_project', 'save_project_as',
  'restore_project_snapshot', 'cascade_delete_bus',
])

function _typed_confirmation_target(tool: string, args: Record<string, unknown>): string | null {
  // The user must type this exact string before Approve is unlocked.
  if (tool === 'delete_project' || tool === 'cascade_delete_bus') {
    return (args.name as string) ?? null
  }
  if (tool === 'save_project' || tool === 'save_project_as') {
    return (args.name as string) ?? null
  }
  if (tool === 'restore_project_snapshot') {
    return (args.snapshot_id as string) ?? null
  }
  return null
}

interface ToolProgressFrame {
  tool_use_id: string
  line: string
  kind: string
}

interface ToolErrorFrame {
  tool_use_id?: string
  tool_name?: string
  error_kind: string
  message: string
}

interface TurnDoneFrame {
  usage?: {
    input_tokens?: number
    output_tokens?: number
    cache_read_tokens?: number
    cache_create_tokens?: number
  }
}

function UsageMeter() {
  const usage = useChatStore((s) => s.usage)
  return (
    <span
      className="font-mono text-[10px] text-muted truncate min-w-0"
      data-testid="chat-usage-meter"
      title="Tokens this session: input / output / read from cache"
    >
      {usage.input_tokens.toLocaleString()} in / {usage.output_tokens.toLocaleString()} out
      {' · '}{usage.cache_read_tokens.toLocaleString()} cached
    </span>
  )
}

function ConfirmationCard() {
  const pending = useChatStore((s) => s.pending)
  const setPending = useChatStore((s) => s.setPending)
  const setError = useChatStore((s) => s.setError)
  const sessionId = useChatStore((s) => s.sessionId)
  const appendMessage = useChatStore((s) => s.appendMessage)
  const [secondsLeft, setSecondsLeft] = useState<number>(0)
  const [typedConfirmation, setTypedConfirmation] = useState<string>('')
  const timerRef = useRef<number | null>(null)

  // Reset the typed-confirmation field whenever the pending card changes.
  useEffect(() => { setTypedConfirmation('') }, [pending?.confirmation_token])

  // Countdown timer. Tracked via ref so a re-render or unmount never leaks.
  useEffect(() => {
    if (timerRef.current != null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (!pending) {
      setSecondsLeft(0)
      return
    }
    const tick = () => {
      const left = Math.max(0, Math.floor((pending.expires_at_epoch_ms - Date.now()) / 1000))
      setSecondsLeft(left)
      if (left <= 0 && timerRef.current != null) {
        clearInterval(timerRef.current)
        timerRef.current = null
        // The countdown used to just stop here, leaving a dead card on
        // screen with Approve still clickable — which 409s
        // `confirmation_expired`. Teaching a user that confirming an expired
        // destructive action is harmless is the one lesson this card must
        // not give. Withdraw it and say why; the agent re-prompts with a
        // fresh token, which is the flow the backend already implements.
        setPending(null)
        setError({
          error_kind: 'confirmation_expired',
          message: `The confirmation for ${pending.tool_name} expired before it was answered. Ask again to retry.`,
        })
      }
    }
    tick()
    timerRef.current = window.setInterval(tick, 250) as unknown as number
    return () => {
      if (timerRef.current != null) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [pending])

  const onApprove = useCallback(async () => {
    if (!pending || !sessionId) return
    try {
      await postChatConfirm(sessionId, {
        token: pending.confirmation_token, decision: 'approve',
      })
      setPending(null)
    } catch (err) {
      toast.error(`confirmation failed: ${(err as Error).message}`)
    }
  }, [pending, sessionId, setPending])

  const onDeny = useCallback(async () => {
    if (!pending || !sessionId) return
    try {
      await postChatConfirm(sessionId, {
        token: pending.confirmation_token, decision: 'deny',
      })
      appendMessage({
        role: 'tool', content: `denied: ${pending.tool_name}`,
        tool_use_id: pending.tool_use_id, tool_name: pending.tool_name,
      })
      setPending(null)
    } catch (err) {
      toast.error(`confirmation failed: ${(err as Error).message}`)
    }
  }, [pending, sessionId, setPending, appendMessage])

  if (!pending) return null

  // Phase 4 — typed confirmation widget for the highest-risk tools.
  // v4-MAJOR-1 / v6-F1: save_project + save_project_as (force-overwrite path),
  // v4-MINOR-1: delete_project, restore_project_snapshot, cascade_delete_bus.
  const requiresTyped = TYPED_CONFIRMATION_TOOLS.has(pending.tool_name)
  const typedTarget = requiresTyped
    ? _typed_confirmation_target(pending.tool_name, pending.args)
    : null
  const typedSatisfied = !requiresTyped || (typedTarget != null && typedConfirmation === typedTarget)

  return (
    <div
      // A destructive action blocking on the user is the strongest reason
      // this panel has to interrupt a screen reader. `alertdialog` both
      // interrupts AND says the thing is interactive — `alert` alone would
      // announce the text and imply there is nothing to do about it.
      // aria-modal is false because focus is deliberately NOT trapped: the
      // card sits inline in the transcript and the user must stay free to
      // scroll back and read what they are approving.
      role="alertdialog"
      aria-modal="false"
      aria-labelledby="chat-confirmation-title"
      className="border border-amber-500/60 bg-amber-500/5 rounded p-3 mx-3 my-2"
      data-testid="chat-confirmation-card"
      data-tool-name={pending.tool_name}
      data-safety-tier={pending.safety_tier}
    >
      <div className="text-[11px] uppercase tracking-wider text-amber-500 mb-1">
        Confirm · {pending.safety_tier}
      </div>
      <div id="chat-confirmation-title" className="text-sm font-medium mb-1 text-text">
        {pending.tool_name}
      </div>
      <pre className="text-[10px] text-muted bg-bg-2 p-2 rounded overflow-x-auto mb-2 whitespace-pre-wrap break-all">
        {JSON.stringify(pending.args, null, 2)}
      </pre>
      {requiresTyped && typedTarget && (
        <div className="mb-2" data-testid="chat-typed-confirmation">
          <div className="text-[10px] text-muted mb-1">
            Type <span className="font-mono text-text">{typedTarget}</span> to confirm:
          </div>
          <input
            className="w-full bg-bg border border-border rounded px-2 py-1 text-xs font-mono"
            value={typedConfirmation}
            onChange={(e) => setTypedConfirmation(e.target.value)}
            placeholder={typedTarget}
            data-testid="chat-typed-confirmation-input"
          />
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          className="px-2 py-1 text-xs rounded bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/50 disabled:opacity-40 disabled:cursor-not-allowed"
          onClick={onApprove}
          disabled={!typedSatisfied}
          data-testid="chat-confirm-approve"
        >
          Approve
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-bg-2 hover:bg-bg-3 border border-border"
          onClick={onDeny}
          data-testid="chat-confirm-deny"
        >
          Deny
        </button>
        <span className="font-mono text-[10px] text-muted ml-auto">
          {secondsLeft}s
        </span>
      </div>
    </div>
  )
}

/**
 * Failures a fresh attempt could plausibly clear on its own.
 *
 * Mirrors `chat_service._RETRYABLE_SDK_KINDS` (rate_limited / upstream_error)
 * rather than inventing a second list, plus the two the server does NOT retry
 * inside a turn but a NEW turn resolves: an unexplained internal_error, and a
 * tool-call cap that resets per turn.
 *
 * Everything else is excluded on purpose. A missing or rejected key, a name
 * collision, a file over the size cap — none of those change because you
 * asked again, and a button that cannot work is worse than no button: it
 * teaches the user the button is a lie.
 */
const RETRYABLE_ERROR_KINDS = new Set([
  'rate_limited', 'upstream_error', 'internal_error', 'tool_call_cap_exceeded',
])

/**
 * Task 14 — single source of truth for the error banner's copy.
 *
 * Used to be three hand-maintained structures that had to agree by hand: a
 * ~28-line `error_kind === 'x' && 'Title'` chain, a NEGATED array of the same
 * ~28 strings gating the raw-kind fall-through, and a third allowlist (below,
 * `TOOL_ERROR_BANNER_KINDS`) deciding which `tool_error` frames get promoted
 * to this banner at all. Adding a kind to the title chain and forgetting the
 * negated list silently printed the title AND the raw kind; the reverse
 * printed nothing. The fall-through below is now DERIVED from this map's
 * keys (`!(kind in KIND_COPY) && kind`), so the two can no longer disagree —
 * see the completeness test in ChatPanel.profile.test.tsx.
 *
 * Every title for a kind that predates this map is copied byte-for-byte from
 * the old per-kind JSX condition it replaces — this migration does not
 * restyle existing copy.
 *
 * SECURITY: no title/body added here may name an identifier (email, user id,
 * org id, project uuid) or a full base_url. The dynamic `error.message` row
 * (rendered unconditionally, unchanged) is the server's own text, already
 * constrained to host:port at most — this map's static copy must not widen
 * that.
 */
export const KIND_COPY: Record<
  string,
  { title: string; body?: string; action?: 'open-settings' | 'new-chat' }
> = {
  project_exists: { title: 'Project name already exists' },
  descendants_exist: { title: 'Project has descendants' },
  confirmation_expired: { title: 'Confirmation expired' },
  rate_limited: { title: 'Rate limited' },
  unauthorized: { title: 'API key rejected' },
  // Fix round 1 (Task 14) — body left unset here on purpose: it's computed
  // in ErrorBanner from the active profile's LABEL (client-side, from
  // useChatProfiles()), not a static string — see `body` in ErrorBanner.
  missing_api_key: { title: 'API key missing' },
  // P-2 — the acting account stopped being active mid-turn.
  inactive_acting_user: { title: 'Account is no longer active' },
  solver_in_flight: { title: 'Solver in flight' },
  parallel_destructive_not_allowed: { title: 'Multiple destructive actions in one turn' },
  tool_call_cap_exceeded: { title: 'Tool call limit reached this turn' },
  // Phase D — chatbot upload error_kinds
  file_too_large: { title: 'File exceeds the 25 MB cap' },
  empty_file: { title: 'Uploaded file is empty' },
  invalid_filename: { title: 'Filename contains invalid characters' },
  unsupported_mime: { title: 'File type not supported' },
  mime_type_mismatch: { title: 'File type mismatch — declared vs. actual content' },
  upload_quota_exceeded: { title: 'Per-project upload quota reached' },
  upload_not_found: { title: 'Referenced upload no longer exists' },
  image_too_large: { title: 'Image exceeds the 10 MB multimodal cap' },
  too_many_multimodal_blocks: { title: 'Too many attachments (max 20)' },
  mime_not_allowlisted_for_multimodal: {
    title: 'File type cannot be attached — use the read tool instead',
  },
  load_not_found: { title: 'Load name not in network' },
  snapshot_count_mismatch: { title: 'Row count does not match network snapshots' },
  snapshot_range_mismatch: { title: 'Time range does not match network snapshots' },
  time_column_parse_error: { title: 'Time column could not be parsed as datetimes' },
  value_column_parse_error: { title: 'Value column has non-numeric data' },
  image_analysis_timeout: { title: 'Vision call timed out (30 s)' },
  vision_invalid_json: { title: 'Vision response was not valid JSON' },
  vision_call_failed: { title: 'Vision call failed' },

  // Task 14 — new kinds, fix-oriented copy.
  unreachable: {
    title: 'Could not reach the model endpoint.',
    body: 'If this is a local endpoint, it likely needs to be started. Check the model settings.',
    action: 'open-settings',
  },
  capability_unsupported: {
    // llm_provider seam + chat_service's capability checks already send a
    // message naming the capability and the profile LABEL (never an
    // id/base_url — see chat_service.py's `capability_unsupported` frames).
    // That renders in the unconditional `error.message` row below, so this
    // entry stays generic and adds no body of its own — inventing a second
    // description would just repeat the server's, or drift from it.
    title: "This model doesn't support that.",
    action: 'open-settings',
  },
  profile_switch_requires_new_chat: {
    title: 'This model needs a fresh chat.',
    action: 'new-chat',
  },
}

/**
 * The subset of KIND_COPY kinds that can arrive on a `tool_error` SSE frame
 * and should be promoted to this banner instead of staying a gray tool-line
 * in the message list.
 *
 * Fix round 1 (Task 14) — CORRECTION, recorded rather than quietly edited:
 * this comment previously claimed `inactive_acting_user` surfaces ONLY on
 * the top-level chat-stream `error` frame and can never arrive as a
 * `tool_error`, and excluded it here on that basis. That claim was
 * investigated and written down as verified, and it was backwards.
 * Re-traced properly this round: `inactive_acting_user` is raised in
 * exactly one place, `_acting()` in `chat_tools.py:1464`. `_acting()` has
 * six call sites, ALL inside tool handlers reached through `_route` /
 * `_authorized_project`. `_dispatch_real_tool_call`
 * (`chat_service.py`, ~line 3824) wraps every handler call in
 * `except Exception`, reads `error_kind` off `exc.detail`, and YIELDS a
 * `tool_error` frame — it does not re-raise, so nothing raised inside a
 * tool handler reaches the top-level `error`-frame catch-all.
 * `inactive_acting_user` can therefore ONLY arrive as a `tool_error`, the
 * opposite of the old claim. Excluding it meant an account deactivated
 * mid-turn showed a generic, truncated gray tool line instead of the
 * "Account is no longer active" banner `KIND_COPY` already had copy for.
 * Included below now, with a routing test
 * (`ChatPanel.profile.test.tsx`) asserting a `tool_error` frame of this
 * kind renders the banner and its title.
 *
 * Every string here is still checked against KIND_COPY by a test so a typo
 * or a stale rename fails loudly instead of silently falling through to
 * the raw kind.
 */
export const TOOL_ERROR_BANNER_KINDS = new Set([
  'project_exists', 'descendants_exist',
  'confirmation_expired', 'rate_limited',
  'unauthorized', 'missing_api_key',
  'inactive_acting_user',
  'solver_in_flight', 'parallel_destructive_not_allowed',
  'tool_call_cap_exceeded',
  // Phase D — upload-tool errors. Same routing as the chat-stream 'error'
  // frame, so a single user mental model handles every failure surface.
  'file_too_large', 'empty_file', 'invalid_filename',
  'unsupported_mime', 'mime_type_mismatch', 'upload_quota_exceeded',
  'upload_not_found', 'image_too_large', 'too_many_multimodal_blocks',
  'mime_not_allowlisted_for_multimodal', 'load_not_found',
  'snapshot_count_mismatch', 'snapshot_range_mismatch',
  'time_column_parse_error', 'value_column_parse_error',
  'image_analysis_timeout', 'vision_invalid_json', 'vision_call_failed',
])

function ErrorBanner({
  onRetry,
  activeProfileLabel,
  sessionProfile,
}: {
  onRetry: () => void
  // Fix round 1 (Task 14) — the brief's `missing_api_key` broadening: body
  // names the ACTIVE profile so a user on a non-Anthropic profile isn't told
  // to paste an Anthropic key. LABEL only, sourced from the same
  // `selectedProfileMeta` the parent already derives from `useChatProfiles()`
  // — never an id or base_url, and no second query added here.
  activeProfileLabel: string | null
  // C-12 — the same profile the body names, passed on to `ApiKeySetup` so the
  // banner's TEXT and its FORM answer one question rather than two. It used to
  // branch on the instance-wide active profile from `/chat/health`, which a
  // member's session may legitimately differ from — so the body could name a
  // local endpoint while the form below it offered to set ANTHROPIC_API_KEY.
  sessionProfile: { id: string; label: string } | null
}) {
  const error = useChatStore((s) => s.error)
  const setError = useChatStore((s) => s.setError)
  const streaming = useChatStore((s) => s.streaming)
  const hasQuestion = useChatStore((s) => s.messages.some((m) => m.role === 'user'))
  const startNewChat = useChatStore((s) => s.startNewChat)
  if (!error) return null

  // v6-F2 — cold-path activate is NOT an error from the user's POV; the
  // backend renders it as success on the agent side (no error frame). If
  // somehow a "cold_path" string reaches the error channel, dismiss silently.
  if (error.error_kind === 'cold_path') {
    setError(null)
    return null
  }

  // v4-MAJOR-1 / v6-F1 project_exists — rendered as a typed banner with a
  // hint that the user should retry with force or a new name. v4-MINOR-1
  // descendants_exist — same shape but with the descendant list.
  const copy = KIND_COPY[error.error_kind] as
    | { title: string; body?: string; action?: 'open-settings' | 'new-chat' }
    | undefined

  // `missing_api_key`'s body can't live as a static KIND_COPY string — it
  // names WHICH profile is missing a key, known only client-side from the
  // profiles query, not from the server's error message. Falls back to no
  // body (old behaviour) when the active profile isn't resolved yet, rather
  // than fabricating a label.
  const body =
    copy?.body ??
    (error.error_kind === 'missing_api_key' && activeProfileLabel
      ? `Currently using the "${activeProfileLabel}" profile.`
      : undefined)

  return (
    <div
      // A turn that failed is an interruption, not a status update — the
      // user is waiting on a reply that is not coming. `alert` announces it
      // without moving focus, which is right here: there is nothing in the
      // banner to operate except the API-key form, and that case renders its
      // own labelled controls.
      role="alert"
      className="border-l-2 border-rose-500 bg-rose-500/5 px-3 py-2 mx-3 my-2 text-xs"
      data-testid="chat-error-banner"
      data-error-kind={error.error_kind}
    >
      <div className="font-medium text-rose-400 mb-1">
        {error.error_kind in KIND_COPY && KIND_COPY[error.error_kind].title}
        {!(error.error_kind in KIND_COPY) && error.error_kind}
      </div>
      <div className="text-muted whitespace-pre-wrap">{error.message}</div>
      {body && (
        <div className="text-muted text-[11px] whitespace-pre-wrap mt-1">{body}</div>
      )}
      {/*
        U-1 — "API key missing" used to be a dead end. In the packaged app it
        was THE state: the bundle ships no `backend/.env` on purpose, so this
        banner was every user's entire experience of the assistant, with
        nothing anywhere to act on. The setup form renders inline here rather
        than on a settings page, because this is where the user is when they
        find out.
      */}
      {error.error_kind === 'missing_api_key' && <ApiKeySetup sessionProfile={sessionProfile} />}
      <div className="flex items-center gap-3 mt-2">
        {/* The failure modes above are the ONLY place a user could previously
            end up with their question on screen, an error on screen, and
            nothing to click. `rate_limited` is transient and self-healing —
            the textbook one-click retry — and it was a dead end. */}
        {RETRYABLE_ERROR_KINDS.has(error.error_kind) && hasQuestion && !streaming && (
          <button
            className="text-[10px] underline text-rose-300 hover:text-rose-200"
            onClick={() => { setError(null); onRetry() }}
            data-testid="chat-error-retry"
          >
            Try again
          </button>
        )}
        {copy?.action === 'open-settings' && (
          <button
            className="text-[10px] underline text-rose-300 hover:text-rose-200"
            onClick={() => {
              // Deep-link straight to the model/profile settings section
              // (Task 15's `requestSettingsSection`/AssistantModelSettings),
              // not just the settings panel in general.
              useUIStore.getState().requestSettingsSection('assistant-model')
              useUIStore.getState().setSlidePanel('settings')
            }}
            data-testid="chat-error-open-settings"
          >
            Open settings
          </button>
        )}
        {copy?.action === 'new-chat' && (
          <button
            className="text-[10px] underline text-rose-300 hover:text-rose-200"
            onClick={() => startNewChat()}
            data-testid="chat-error-start-new-chat"
          >
            Start new chat
          </button>
        )}
        <button
          className="text-[10px] underline text-muted hover:text-text"
          onClick={() => setError(null)}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────
// Phase D — Upload chip strip + replayed-message attachment chips
// ──────────────────────────────────────────────────────────────────────

function _mimeIcon(mime: string): string {
  if (mime.startsWith('image/')) return '🖼️'
  if (mime === 'application/pdf') return '📄'
  if (mime.includes('spreadsheet') || mime === 'text/csv') return '📊'
  if (mime.includes('wordprocessingml')) return '📝'
  if (mime === 'text/markdown' || mime === 'text/plain') return '📝'
  return '📎'
}

function _formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface UploadChipStripProps {
  uploads: UploadMetaUI[]
  attachedFileIds: string[]
  toggleAttached: (fileId: string) => void
  onDelete: (fileId: string, filename: string) => void
  currentProject: string | null
}

function UploadChipStrip({
  uploads, attachedFileIds, toggleAttached, onDelete, currentProject,
}: UploadChipStripProps) {
  if (!currentProject || uploads.length === 0) return null
  const attachedSet = new Set(attachedFileIds)
  return (
    <div
      className="border-t border-border bg-bg-2/60 px-2 py-1.5 shrink-0 max-h-32 overflow-y-auto"
      data-testid="chat-upload-chip-strip"
      aria-live="polite"
    >
      <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
        Files in this project ({uploads.length})
      </div>
      <div className="flex flex-wrap gap-1.5">
        {uploads.map((u) => {
          const attached = attachedSet.has(u.file_id)
          const isExport = u.kind === 'agent_export'
          return (
            <div
              key={u.file_id}
              className={
                'flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] ' +
                (isExport
                  ? 'bg-accent/15 border-accent/40 '
                  : 'bg-bg border-border ') +
                (attached ? '' : 'opacity-50')
              }
              data-testid="chat-upload-chip"
              data-kind={u.kind}
              data-attached={attached ? 'true' : 'false'}
              title={
                attached
                  ? `${u.filename} (${_formatSize(u.size)}) — attached to next message`
                  : `${u.filename} (${_formatSize(u.size)}) — NOT attached`
              }
            >
              <input
                type="checkbox"
                className="h-3 w-3 accent-accent cursor-pointer"
                checked={attached}
                onChange={() => toggleAttached(u.file_id)}
                aria-label={`Attach ${u.filename} to next message`}
              />
              <span>{_mimeIcon(u.mime)}</span>
              <span className="truncate max-w-[200px]" title={u.filename}>
                {u.filename}
              </span>
              <span className="text-muted text-[10px]">
                {_formatSize(u.size)}
              </span>
              {/* Phase D polish #4 — PDF page-count + truncation badge. */}
              {u.mime === 'application/pdf' && u.page_count != null && (
                u.truncated_to_100_pages ? (
                  <span
                    className="text-[10px] px-1 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-400"
                    title={`Only the first 100 of ${u.page_count} pages will be attached to the next message. The full file remains on disk and can be downloaded.`}
                    data-testid="chat-chip-pdf-truncated"
                  >
                    ⚠ 100 / {u.page_count} pgs
                  </span>
                ) : (
                  <span
                    className="text-[10px] text-muted"
                    title={`${u.page_count} pages — well under the 100-page cap.`}
                  >
                    {u.page_count} pgs
                  </span>
                )
              )}
              {isExport && (
                <a
                  href={getUploadBlobUrl(currentProject, u.file_id)}
                  download={u.filename}
                  className="text-accent hover:underline px-0.5"
                  title={`Download ${u.filename}`}
                  aria-label={`Download ${u.filename}`}
                  data-testid="chat-chip-download"
                >
                  ↓
                </a>
              )}
              <button
                className="text-muted hover:text-rose-400 px-0.5"
                onClick={() => onDelete(u.file_id, u.filename)}
                title={`Delete ${u.filename}`}
                aria-label={`Delete ${u.filename}`}
                data-testid="chat-chip-delete"
              >
                ×
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * Collapsible live log for long-running tools (solver PHASE/VALIDATION
 * lines, import progress). Progress is retained after the tool completes
 * so users can re-open the panel under a ✓ row; the Zustand slice caps
 * lines per tool_use_id (see appendToolProgress).
 */
function ToolProgressDetails({ toolUseId }: { toolUseId: string }) {
  const lines = useChatStore((s) => s.toolProgress[toolUseId] ?? EMPTY_TOOL_PROGRESS)
  if (lines.length === 0) return null
  return (
    <details
      className="mt-1 rounded border border-border/60 bg-bg-2/30"
      data-testid="chat-tool-progress"
      data-tool-use-id={toolUseId}
    >
      <summary className="cursor-pointer select-none px-2 py-1 text-[10px] text-muted hover:text-text">
        Progress ({lines.length} line{lines.length === 1 ? '' : 's'})
      </summary>
      <pre
        className="max-h-48 overflow-y-auto px-2 pb-2 text-[10px] leading-snug text-muted whitespace-pre-wrap break-all"
        data-testid="chat-tool-progress-lines"
      >
        {lines.map((row, i) => (
          <div key={`${i}-${row.kind}`}>
            {row.kind ? `[${row.kind}] ` : ''}{row.line}
          </div>
        ))}
      </pre>
    </details>
  )
}

const EMPTY_TOOL_PROGRESS: { kind: string; line: string }[] = []

// `ChatEmptyState` used to live here — the no-project primer, with the two
// CustomEvent buttons Sidebar listens for. It is now the no-project BRANCH of
// ChatLaunchGreeting, which carries both its copy and its testids, so the
// event bridge (`chat:open-project-picker` / `chat:open-new-project-wizard`)
// is unchanged and Sidebar needed no edit.

/**
 * The three things people need from a message and could not do: take the
 * answer with them, ask again, or fix the question.
 *
 * Hidden while a turn is streaming — but only retry and edit. Both rewind the
 * SERVER history, and `rewind_session` refuses under `_turn_in_flight`, so
 * offering them mid-turn would clear the screen and silently leave the model's
 * context untouched: the worst outcome, because it looks like it worked. Copy
 * touches nothing and stays.
 *
 * Tool rows get nothing. Their content is a synthetic one-line summary of a
 * call, not something anyone wants on a clipboard or re-asked on its own.
 */
function MessageActions({ message, streaming, onCopy, onRetry, onEdit }: {
  message: ChatMessage
  streaming: boolean
  onCopy: (m: ChatMessage) => void
  onRetry: (m: ChatMessage) => void
  onEdit: (m: ChatMessage) => void
}) {
  if (message.role === 'tool') return null
  const btn = 'px-1 py-0.5 text-[10px] rounded text-muted hover:text-accent hover:bg-panel transition-colors'
  return (
    <div className="flex items-center gap-1 mt-1 opacity-60 hover:opacity-100 transition-opacity">
      <button
        className={btn}
        onClick={() => onCopy(message)}
        title="Copy this message"
        aria-label="Copy this message"
        data-testid={`chat-copy-${message.id}`}
      >
        Copy
      </button>
      {!streaming && message.role === 'assistant' && (
        <button
          className={btn}
          onClick={() => onRetry(message)}
          title="Discard this answer and ask again"
          aria-label="Retry this answer"
          data-testid={`chat-retry-${message.id}`}
        >
          Retry
        </button>
      )}
      {!streaming && message.role === 'user' && (
        <button
          className={btn}
          onClick={() => onEdit(message)}
          title="Put this question back in the composer to change it"
          aria-label="Edit this question"
          data-testid={`chat-edit-${message.id}`}
        >
          Edit
        </button>
      )}
    </div>
  )
}

/** Starter prompts when no project is loaded. */
const CHAT_STARTER_PROMPTS_UNBOUND: { label: string; text: string }[] = [
  {
    label: 'Open a project',
    text: 'List my projects and open project_name',
  },
  {
    label: 'Browse projects',
    text: 'Open the project picker',
  },
]

/** Starter prompts when a project is loaded but the conversation is empty. */
const CHAT_STARTER_PROMPTS: { label: string; text: string }[] = [
  {
    label: 'Compare two scenarios',
    text: 'Compare scenario_a vs scenario_b on total cost and open the compare rail',
  },
  {
    label: 'Open Economics',
    text: 'Open the Results Economics tab',
  },
  {
    label: 'Summarize this solve',
    text: 'Summarize the key results of the current project',
  },
]

function ChatStarterChips({
  prompts,
  onPick,
  disabled,
}: {
  prompts: { label: string; text: string }[]
  onPick: (text: string) => void
  disabled?: boolean
}) {
  return (
    <div
      className="m-3 mt-4"
      data-testid="chat-starter-chips"
    >
      <div className="text-[11px] text-muted mb-2">Try asking</div>
      <div className="flex flex-wrap gap-1.5">
        {prompts.map((p) => (
          <button
            key={p.label}
            type="button"
            disabled={disabled}
            className="px-2.5 py-1 text-[11px] rounded border border-border bg-bg-2/60 text-text hover:bg-bg-3/50 hover:border-accent/40 disabled:opacity-50 disabled:pointer-events-none transition-colors"
            onClick={() => onPick(p.text)}
            data-testid="chat-starter-chip"
            title={p.text}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function ReplayAttachmentChips({ fileIds }: { fileIds: string[] }) {
  // Look up filename + kind from the live uploads slice. Missing files
  // (deleted since the turn) render as muted "Attachment no longer
  // available" chips so the conversation reads consistently.
  const uploads = useChatStore((s) => s.uploads)
  if (!fileIds || fileIds.length === 0) return null
  return (
    <div
      className="flex flex-wrap gap-1 mt-1 text-[10px]"
      data-testid="chat-replay-attachments"
    >
      {fileIds.map((fid) => {
        const u = uploads.find((x) => x.file_id === fid)
        if (!u) {
          return (
            <span
              key={fid}
              className="px-1.5 py-0.5 rounded border border-border bg-bg-2 text-muted italic"
            >
              📎 attachment unavailable
            </span>
          )
        }
        return (
          <span
            key={fid}
            className="px-1.5 py-0.5 rounded border border-border bg-bg-2 text-muted"
            title={u.filename}
          >
            {_mimeIcon(u.mime)} {u.filename}
          </span>
        )
      })}
    </div>
  )
}


export default function ChatPanel() {
  const qc = useQueryClient()
  // tool_use_id → safety_tier, written at tool_request, consumed at
  // tool_result / tool_error. `tool_result` frames don't carry the tier, so
  // this map is how a completion knows whether it mutated (assetWrite fix).
  const toolTierRef = useRef(new Map<string, string>())
  const sessionId = useChatStore((s) => s.sessionId)
  const setSessionId = useChatStore((s) => s.setSessionId)
  const profileId = useChatStore((s) => s.profileId)
  const setProfileId = useChatStore((s) => s.setProfileId)
  const startNewChat = useChatStore((s) => s.startNewChat)
  // Fix round 1 — the hydration effect's dependency; see that effect's
  // comment for why this exists instead of keying on `sessionId`.
  const newChatSeq = useChatStore((s) => s.newChatSeq)
  const messages = useChatStore((s) => s.messages)
  const appendMessage = useChatStore((s) => s.appendMessage)
  const appendTokenDelta = useChatStore((s) => s.appendTokenDelta)
  const appendThinkingDelta = useChatStore((s) => s.appendThinkingDelta)
  const setMessages = useChatStore((s) => s.setMessages)
  const setPending = useChatStore((s) => s.setPending)
  const appendToolProgress = useChatStore((s) => s.appendToolProgress)
  const accrueUsage = useChatStore((s) => s.accrueUsage)
  const streaming = useChatStore((s) => s.streaming)
  const setStreaming = useChatStore((s) => s.setStreaming)
  const setError = useChatStore((s) => s.setError)
  const setStreamCleanup = useChatStore((s) => s.setStreamCleanup)
  const closeStream = useChatStore((s) => s.closeStream)

  const currentProject = useUIStore((s) => s.currentProject)
  // Read only for the autoscroll effect below — see the dependency-array
  // comment there for why AssistantDock's collapsed state has to be visible
  // here at all.
  const assistantDockOpen = useUIStore((s) => s.assistantDockOpen)
  const assistantSpeakEnabled = useUIStore((s) => s.assistantSpeakEnabled)
  const toggleAssistantSpeak = useUIStore((s) => s.toggleAssistantSpeak)
  const resetChatForProjectSwitch = useChatStore((s) => s.resetForProjectSwitch)
  const prevProjectRef = useRef<string | null | undefined>(undefined)

  // #20 — what the last reload recovered. Component state rather than the
  // chat store: both are facts about one hydration, not about the
  // conversation, and neither should survive a project switch or be
  // rehydrated into a transcript.
  const [historyGap, setHistoryGap] = useState<number>(0)
  const [interruptedTurn, setInterruptedTurn] = useState<InterruptedTurn | null>(null)

  // Reset chat state on project switch (mirrors simulationStore pattern).
  useEffect(() => {
    if (prevProjectRef.current !== undefined && prevProjectRef.current !== currentProject) {
      resetChatForProjectSwitch()
    }
    prevProjectRef.current = currentProject
  }, [currentProject, resetChatForProjectSwitch])

  // Hydrate from chat.jsonl whenever a project becomes active. The backend
  // also rehydrates `session.messages` so subsequent turns can thread prior
  // context into the Anthropic SDK AND benefit from prompt caching (the
  // cache is per-session, so reusing the session_id keeps the cache warm).
  //
  // BEHAVIOUR CHANGE, recorded deliberately: now that AssistantDock mounts
  // ChatPanel for the app's lifetime, this fires at boot for EVERY user with a
  // project open — including one who never opens the assistant — where it
  // previously waited until the 'chat' slide panel was opened. Same for the
  // uploads hydration further down. Both are one GET each, both swallow their
  // errors, and the transcript they replay lands in chatStore rather than on
  // screen, so the cost is a request and some memory, not a failure mode.
  // Deliberately NOT made lazy here: gating hydration on first-open is a
  // design change (it needs a "has the user ever opened the dock" concept and
  // changes when the session_id becomes available for prompt caching), and
  // this branch is a bug fix. Revisit if boot latency is ever measured to care.
  useEffect(() => {
    if (!currentProject) return
    // Task 13 — `startNewChat()` (the cross-wire profile-switch confirm, and
    // the header's "New chat" button) clears `messages` and arms
    // `suppressHydrationOnce`, consumed here unconditionally before the
    // messages-length guard: without this, the freshly-cleared store (0
    // messages, exactly the condition the guard below lets through) would
    // re-hydrate the OLD `last_session_id` the next time this effect runs,
    // undoing "start a new chat" immediately.
    //
    // Fix round 1 — this effect's dependency array watches `newChatSeq`, NOT
    // `sessionId`. It used to watch `sessionId`, which broke when
    // `startNewChat()` fired while `sessionId` was ALREADY null (a fresh
    // project with no chat.jsonl yet, or a cross-wire pick before the user's
    // first message): a null→null "change" that React's dependency
    // comparison never sees, so the effect never reran to consume the flag.
    // The flag then survived to the NEXT real trigger — a genuine project
    // switch — and silently suppressed THAT project's real history load.
    // `newChatSeq` is a counter `startNewChat()` bumps unconditionally on
    // every call, so it always changes and the effect always gets a chance
    // to consume the flag before anything else can observe it.
    if (useChatStore.getState().consumeSuppressHydrationOnce()) return
    // Only seed an EMPTY conversation. Replaying chat.jsonl over a store that
    // already holds a conversation erases the turn the user just watched
    // arrive, because a turn is only persisted once it completes. The store is
    // authoritative while it holds a conversation; disk is the seed for a
    // fresh one. `resetForProjectSwitch` empties it on a real project change,
    // which is what re-arms this.
    //
    // The guard is still live even though the panel no longer remounts on
    // navigation (it is mounted for the app's lifetime inside AssistantDock).
    // This effect re-runs whenever `currentProject` OR `newChatSeq` changes
    // (the latter added for the `startNewChat` guard above) AND on every
    // mount, and the mounts that remain all reach it with a populated store:
    // the dock's ErrorBoundary swapping back to its children after a Retry,
    // HMR in dev, and a project switch whose reset has not landed yet. Do not
    // conclude the early return is dead — ChatPanel.test.tsx's "does not wipe
    // an in-flight turn when the panel is reopened" fails without it.
    if (useChatStore.getState().messages.length > 0) return
    let cancelled = false
    getChatHistory().then((h) => {
      if (cancelled) return
      // Re-check: a turn may have started while the fetch was in flight.
      if (useChatStore.getState().messages.length > 0) return
      const turns = h.turns ?? []
      // Build a flat message list mirroring the SSE event order: each turn
      // contributes a user bubble then an assistant bubble.
      const seeded: typeof messages = []
      let counter = 0
      for (const t of turns) {
        if (t.user) {
          seeded.push({
            id: `replay-u-${counter++}`,
            role: 'user',
            content: t.user,
            attachment_file_ids: t.attachment_file_ids,
            ts: Math.round((t.ts || 0) * 1000),
          })
        }
        // Concatenate text blocks; tool_use blocks render as a compact tag.
        let assistantText = ''
        for (const block of (t.assistant || [])) {
          const btype = (block as Record<string, unknown>).type
          if (btype === 'text') {
            assistantText += String((block as Record<string, unknown>).text ?? '')
          } else if (btype === 'tool_use') {
            const name = String((block as Record<string, unknown>).name ?? '?')
            assistantText += `\n[tool: ${name}]`
          }
        }
        if (assistantText.trim()) {
          seeded.push({
            id: `replay-a-${counter++}`,
            role: 'assistant',
            content: assistantText,
            ts: Math.round((t.ts || 0) * 1000),
          })
        }
      }
      setMessages(seeded)
      if (h.last_session_id) {
        setSessionId(h.last_session_id)
      }
      // #20 — the backend detects both of these and reports them exactly
      // once. Dropping them here would make that whole recovery path
      // invisible: the user would see a shorter conversation than they had,
      // or a message of theirs simply missing, with nothing to explain it.
      setHistoryGap(h.history_gap ?? 0)
      setInterruptedTurn(h.pending_turn ?? null)
    }).catch(() => { /* missing chat.jsonl is fine — first time on this project */ })
    return () => { cancelled = true }
  }, [currentProject, newChatSeq, setMessages, setSessionId])

  // Phase D — upload slice + send-attach wiring.
  const uploads = useChatStore((s) => s.uploads)
  const setUploads = useChatStore((s) => s.setUploads)
  const addUpload = useChatStore((s) => s.addUpload)
  const removeUpload = useChatStore((s) => s.removeUpload)
  const attachedFileIds = useChatStore((s) => s.attachedFileIds)
  const toggleAttached = useChatStore((s) => s.toggleAttached)
  const setAttachedFileIds = useChatStore((s) => s.setAttachedFileIds)
  const unseenExportCount = useChatStore((s) => s.unseenExportCount)
  const bumpUnseenExport = useChatStore((s) => s.bumpUnseenExport)
  const clearUnseenExports = useChatStore((s) => s.clearUnseenExports)

  // Hydrate the uploads list whenever a project becomes active. The chip
  // strip mirrors what's on disk so a tab switch doesn't lose previously-
  // uploaded files. We don't auto-attach any of these; only freshly
  // uploaded files default to checked-ON.
  //
  // Also boot-time for every user now that the panel is always mounted — see
  // the note on the chat.jsonl hydration above for why that is accepted here
  // rather than made lazy.
  useEffect(() => {
    if (!currentProject) {
      setUploads([])
      return
    }
    let cancelled = false
    listUploads(currentProject)
      .then((list) => {
        if (cancelled) return
        const slice: UploadMetaUI[] = list.map((m) => ({
          file_id: m.file_id, filename: m.filename, mime: m.mime,
          size: m.size, kind: m.kind, uploaded_at: m.uploaded_at,
          page_count: m.page_count, truncated_to_100_pages: m.truncated_to_100_pages,
        }))
        setUploads(slice)
        // Reset attach selection to ALL freshly-loaded files attached
        // by default — matches the "default-ON" rule in addUpload.
        setAttachedFileIds(slice.map((u) => u.file_id))
      })
      .catch(() => { /* empty / 404 — no uploads yet */ })
    return () => { cancelled = true }
  }, [currentProject, setUploads, setAttachedFileIds])

  // ── File upload primitives ────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleUploadFiles = useCallback(async (files: FileList | File[]) => {
    if (!currentProject) {
      toast.error('Load a project before uploading files.')
      return
    }
    const list = Array.from(files)
    // Phase D polish #2 — only wrap in a batch toast when >1 file is
    // submitted. Single-file uploads keep the existing simple toast path.
    const useBatch = list.length > 1
    const batchId = useBatch ? `batch-${Date.now()}` : null
    const toastIdRef = { current: '' as string }
    const { beginUploadBatch, markUploadRow, endUploadBatch } = useChatStore.getState()
    if (useBatch && batchId) {
      beginUploadBatch(
        batchId,
        list.map((f) => ({ name: f.name, size: f.size })),
      )
      toastIdRef.current = toast.custom(
        (t) => (
          <UploadProgressToast batchId={batchId} toastId={t.id} />
        ),
        {
          id: batchId,
          duration: Infinity,
          position: 'bottom-right',
        },
      ) as string
    }
    // Track row IDs separately so duplicate filenames don't collide with
    // the dedup logic inside beginUploadBatch.
    const rowIds = new Map<File, string>()
    if (useBatch && batchId) {
      const batch = useChatStore.getState().uploadBatches[batchId]
      list.forEach((f, i) => {
        rowIds.set(f, batch?.rows[i]?.rowId ?? f.name)
      })
    }
    for (const file of list) {
      const rowId = rowIds.get(file) ?? file.name
      if (file.size > UPLOAD_MAX_BYTES) {
        if (useBatch && batchId) {
          markUploadRow(batchId, rowId, {
            status: 'failed',
            errorKind: 'file_too_large',
            errorMessage: `${(file.size / (1024 * 1024)).toFixed(1)} MB > 25 MB cap`,
          })
        } else {
          toast.error(
            `${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB) ` +
            'exceeds the 25 MB upload cap.',
          )
        }
        continue
      }
      try {
        if (useBatch && batchId) {
          markUploadRow(batchId, rowId, { status: 'uploading' })
        }
        const before = useChatStore.getState().uploads.length
        const meta = await uploadFile(currentProject, file)
        addUpload({
          file_id: meta.file_id, filename: meta.filename,
          mime: meta.mime, size: meta.size, kind: meta.kind,
          uploaded_at: meta.uploaded_at,
          page_count: meta.page_count,
          truncated_to_100_pages: meta.truncated_to_100_pages,
        })
        const after = useChatStore.getState().uploads.length
        if (useBatch && batchId) {
          markUploadRow(batchId, rowId, { status: 'ok' })
        } else if (after > before) {
          toast.success(`Uploaded ${meta.filename}`)
        } else {
          toast(`${meta.filename} already attached`, { icon: 'ℹ️' })
        }
      } catch (err) {
        const detail = (err as UploadError).detail
        const kind = detail?.error_kind ?? 'upload_failed'
        const message = detail?.message ?? (err as Error).message
        if (useBatch && batchId) {
          markUploadRow(batchId, rowId, {
            status: 'failed',
            errorKind: kind,
            errorMessage: message,
          })
        } else {
          toast.error(`${file.name}: ${kind} — ${message}`)
        }
      }
    }
    if (useBatch && batchId) {
      // Tear down the batch + dismiss the toast after a brief delay so the
      // user sees the final state before it disappears. Failed-row batches
      // hang around longer so the user can read the error_kind.
      const batchAfter = useChatStore.getState().uploadBatches[batchId]
      const anyFailed = batchAfter?.rows.some((r) => r.status === 'failed') ?? false
      window.setTimeout(() => {
        toast.dismiss(toastIdRef.current)
        endUploadBatch(batchId)
      }, anyFailed ? 8000 : 3000)
    }
  }, [currentProject, addUpload])

  const onClickFilePicker = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const onFileInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleUploadFiles(files)
    }
    // Reset the input so re-selecting the same file fires onChange.
    e.target.value = ''
  }, [handleUploadFiles])

  const handlePasteFromClipboard = useCallback(async () => {
    if (!navigator.clipboard?.read) {
      toast.error('Clipboard read API not available in this browser.')
      return
    }
    try {
      const items = await navigator.clipboard.read()
      for (const item of items) {
        // Prefer image types; PDF is rarely in the clipboard but accept it
        // when present.
        const imgType = item.types.find(
          (t) => t === 'image/png' || t === 'image/jpeg' || t === 'image/webp',
        )
        const pdfType = item.types.find((t) => t === 'application/pdf')
        const pickType = imgType ?? pdfType
        if (!pickType) {
          toast('Clipboard has no image or PDF — paste text with Ctrl+V.', { icon: 'ℹ️' })
          continue
        }
        const blob = await item.getType(pickType)
        const ts = Date.now()
        const ext = pickType.split('/').pop() || 'bin'
        const file = new File([blob], `pasted-${ts}.${ext}`, { type: pickType })
        await handleUploadFiles([file])
      }
    } catch (err) {
      toast.error(`Paste failed: ${(err as Error).message}`)
    }
  }, [handleUploadFiles])

  // ── Drag-drop overlay state ───────────────────────────────────────────
  //
  // Phase D polish #5 — silently disabled on coarse-pointer (touch) devices
  // where drag-drop from the OS file picker doesn't fire `dragenter`
  // anyway. The touch-hint banner below points users at the file-picker
  // and paste buttons instead.
  const isCoarsePointer = useIsCoarsePointer()
  const [dragActive, setDragActive] = useState(false)
  // dwellTimerRef debounces dragLeave so a quick child-element traversal
  // doesn't flicker the overlay off-and-on.
  const dragLeaveTimerRef = useRef<number | null>(null)
  const onDragEnter = useCallback((e: React.DragEvent) => {
    if (isCoarsePointer) return
    if (!e.dataTransfer?.types?.includes('Files')) return
    e.preventDefault()
    if (dragLeaveTimerRef.current != null) {
      clearTimeout(dragLeaveTimerRef.current)
      dragLeaveTimerRef.current = null
    }
    setDragActive(true)
  }, [isCoarsePointer])
  const onDragOver = useCallback((e: React.DragEvent) => {
    if (isCoarsePointer) return
    if (!e.dataTransfer?.types?.includes('Files')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }, [isCoarsePointer])
  const onDragLeave = useCallback((e: React.DragEvent) => {
    if (isCoarsePointer) return
    e.preventDefault()
    // Short dwell guard — 80ms is enough for child-element traversal to
    // settle without the user noticing.
    dragLeaveTimerRef.current = window.setTimeout(() => {
      setDragActive(false)
      dragLeaveTimerRef.current = null
    }, 80) as unknown as number
  }, [isCoarsePointer])
  const onDrop = useCallback((e: React.DragEvent) => {
    if (isCoarsePointer) return
    e.preventDefault()
    setDragActive(false)
    if (dragLeaveTimerRef.current != null) {
      clearTimeout(dragLeaveTimerRef.current)
      dragLeaveTimerRef.current = null
    }
    const files = e.dataTransfer?.files
    if (files && files.length > 0) {
      handleUploadFiles(files)
    }
  }, [isCoarsePointer, handleUploadFiles])

  // Phase D polish #5 — one-time touch hint above the prompt area.
  // Persists dismissal in localStorage so a returning user doesn't see it
  // every visit. Auto-dismisses 5 s after first paint.
  const [touchHintVisible, setTouchHintVisible] = useState(false)
  useEffect(() => {
    if (!isCoarsePointer) return
    if (readPref('chat:touchHintShown') === '1') return
    setTouchHintVisible(true)
    const timer = window.setTimeout(() => {
      setTouchHintVisible(false)
      writePref('chat:touchHintShown', '1')
    }, 5000)
    return () => clearTimeout(timer)
  }, [isCoarsePointer])
  const dismissTouchHint = useCallback(() => {
    setTouchHintVisible(false)
    writePref('chat:touchHintShown', '1')
  }, [])

  const onDeleteChip = useCallback(async (fileId: string, filename: string) => {
    if (!currentProject) return
    try {
      await deleteUpload(currentProject, fileId)
      removeUpload(fileId)
      toast.success(`Deleted ${filename}`)
    } catch (err) {
      toast.error(`Delete failed: ${(err as Error).message}`)
    }
  }, [currentProject, removeUpload])

  // Detect agent-exported uploads that appeared since the last list
  // hydration → bump the unseen-export counter. The user "acknowledges"
  // an export by clicking the chip strip header (clearUnseenExports).
  const knownExportIdsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    const current = new Set(
      uploads.filter((u) => u.kind === 'agent_export').map((u) => u.file_id),
    )
    let fresh = 0
    for (const id of current) {
      if (!knownExportIdsRef.current.has(id)) fresh += 1
    }
    // Only fire AFTER initial hydration — we don't want to bump on the
    // first listUploads result that surfaces files from a previous session.
    if (knownExportIdsRef.current.size > 0) {
      for (let i = 0; i < fresh; i++) bumpUnseenExport()
    }
    knownExportIdsRef.current = current
  }, [uploads, bumpUnseenExport])

  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Whether the pending composer text arrived by voice. `speech.listening` is
  // not a substitute: the mic is stopped before the send fires (Enter stops
  // dictation), so by the time we build the request it always reads false.
  // Cleared on a manual keystroke and after every send, so "I dictated, then
  // rewrote it by hand" counts as typed — which matches what the user did
  // last, and is the safer default for a feature that decides whether the
  // machine talks out loud.
  const dictatedRef = useRef(false)
  // Whether the turn currently in flight was dictated. Separate from
  // `dictatedRef`, which describes the COMPOSER and is cleared by the send —
  // by the time the answer lands, the composer has been empty for a while.
  const voiceTurnRef = useRef(false)

  const onSpeechFinal = useCallback((text: string) => {
    dictatedRef.current = true
    setInput((prev) => {
      const el = textareaRef.current
      const start = el?.selectionStart ?? prev.length
      const end = el?.selectionEnd ?? prev.length
      const next = insertAtCursor(prev, start, end, text, { padSpace: true })
      requestAnimationFrame(() => {
        const ta = textareaRef.current
        if (!ta) return
        ta.focus()
        ta.setSelectionRange(next.selectionStart, next.selectionEnd)
      })
      return next.value
    })
  }, [])

  const speech = useSpeechToText({
    enabled: !streaming,
    onFinal: onSpeechFinal,
    onError: (msg) => toast.error(msg),
  })

  // Stop the mic across project switches and while a turn is streaming.
  useEffect(() => {
    speech.stop()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to project identity
  }, [currentProject])

  // Stop dictation when the dock collapses.
  //
  // This replaces a guarantee the branch removed. While ChatPanel was the
  // 'chat' SlidePanel, closing it unmounted the panel and useSpeechToText's
  // `useEffect(() => () => stop(), [stop])` turned the microphone off. The
  // panel is now deliberately never unmounted, so that cleanup no longer
  // fires — and SpeechSession sets `continuous = true` with an `onend`
  // auto-restart, so the session runs indefinitely once started.
  //
  // What made it serious rather than untidy: the mic button's active state
  // and the interim-transcript line are both inside the dock's `hidden` body,
  // so a user who starts dictating, collapses the dock and walks away gets no
  // in-app signal at all that the microphone is still recording. The OS
  // indicator is the only remaining cue, and it is weakest in the packaged
  // WKWebView build.
  //
  // STOP, not disable. `enabled` stays `!streaming`, so expanding the dock
  // again and clicking the mic works exactly as before — this ends the
  // current session, it does not make dictation unavailable while collapsed.
  useEffect(() => {
    if (!assistantDockOpen) speech.stop()
  }, [assistantDockOpen, speech.stop])

  useEffect(() => {
    if (!speech.listening) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        speech.stop()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [speech.listening, speech.stop])

  // Prompt-area height: user-resizable via the top drag handle, persisted to
  // localStorage so it survives page reloads. The min/max guardrails keep the
  // panel usable even if a careless drag would shrink it to 0 or grow it past
  // the message list.
  const PROMPT_MIN_H = 60
  const PROMPT_MAX_H = 360
  const PROMPT_DEFAULT_H = 88
  const [promptHeight, setPromptHeight] = useState<number>(() => {
    const stored = Number(readPref('chat:promptHeight') || NaN)
    return Number.isFinite(stored) && stored >= PROMPT_MIN_H && stored <= PROMPT_MAX_H
      ? stored
      : PROMPT_DEFAULT_H
  })
  useEffect(() => {
    writePref('chat:promptHeight', String(promptHeight))
  }, [promptHeight])
  const dragStartRef = useRef<{ y: number; h: number } | null>(null)
  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragStartRef.current = { y: e.clientY, h: promptHeight }
    const onMove = (ev: MouseEvent) => {
      const s = dragStartRef.current
      if (!s) return
      // Mouse moves DOWN → prompt shrinks; UP → prompt grows. The drag handle
      // lives at the prompt's TOP edge, so the inverse mapping is correct.
      const next = Math.max(PROMPT_MIN_H, Math.min(PROMPT_MAX_H, s.h - (ev.clientY - s.y)))
      setPromptHeight(next)
    }
    const onUp = () => {
      dragStartRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [promptHeight])

  // Lock-to-bottom autoscroll: only follow new messages when the user is
  // already near the bottom. Scrolling up mid-stream must not yank the view
  // back down on every token/tool batch.
  const [stickToBottom, setStickToBottom] = useState(true)
  const [showJumpLatest, setShowJumpLatest] = useState(false)
  const messagesScrollRef = useRef<HTMLDivElement | null>(null)
  const pendingTokenForScroll = useChatStore((s) => s.pending?.confirmation_token ?? null)

  const scrollToLatest = useCallback(() => {
    setStickToBottom(true)
    setShowJumpLatest(false)
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [])

  const onMessagesScroll = useCallback(() => {
    const el = messagesScrollRef.current
    if (!el) return
    const nearBottom = isNearBottom(el, 80)
    setStickToBottom(nearBottom)
    if (nearBottom) setShowJumpLatest(false)
  }, [])

  // `assistantDockOpen` is a dependency, not just a read, because of
  // AssistantDock: while the dock is collapsed this panel sits under a
  // `display:none` ancestor (kept mounted so a streaming turn survives the
  // collapse — see AssistantDock.tsx), and an element with no layout box
  // cannot be scrolled. `scrollIntoView` calls that land while collapsed are
  // silent no-ops in a real browser (not just jsdom), and none of the other
  // deps here change on an expand-only click, so without this the effect
  // would never re-run and the transcript could sit scrolled to wherever it
  // last had layout — "ask a question, collapse, the answer streams in,
  // expand" would land on stale scroll position instead of the latest token.
  //
  // This does not override a deliberate scroll-up: `stickToBottom` already
  // gates the branch below, and nothing here touches it. Collapsing hides
  // the scroll container, so the user cannot fire onMessagesScroll while
  // it's hidden — whatever `stickToBottom` was at collapse time is exactly
  // what it still is on expand, and the existing if/else already respects
  // it (bottom-follow if they were following, only the "jump to latest"
  // affordance if they'd scrolled up). Expanding just gives the same
  // decision a chance to actually run once there's a box to scroll.
  useEffect(() => {
    if (stickToBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
      setShowJumpLatest(false)
    } else {
      setShowJumpLatest(true)
    }
  }, [messages.length, pendingTokenForScroll, stickToBottom, assistantDockOpen])

  // Frame handler — translates SSE frames into chatStore updates.
  const handleFrame = useCallback((frame: ChatFrame) => {
    switch (frame.event) {
      case 'session_init': {
        const d = _frame_data<SessionInitFrame>(frame)
        setSessionId(d.session_id)
        // The dropdown's fallback display (`profileId ?? active_profile_id`)
        // is only as fresh as its last fetch — refetch on every new session
        // so an admin's `set_active_profile` elsewhere, or a prior turn's A8
        // fallback, shows up without the user having to reopen the panel.
        // Deliberately NOT `setProfileId(d.profile_id)`: the store's selector
        // stays `null` (follow-the-server) unless the user picks one.
        qc.invalidateQueries({ queryKey: CHAT_PROFILES_QUERY_KEY })
        break
      }
      case 'token': {
        const d = _frame_data<TokenFrame>(frame)
        // Accumulate into the active assistant bubble instead of spawning
        // one bubble per delta — keeps the message list readable when
        // Sonnet streams 2k tokens of output.
        appendTokenDelta(d.delta)
        break
      }
      case 'thinking': {
        const d = _frame_data<ThinkingFrame>(frame)
        appendThinkingDelta(d.delta)
        break
      }
      case 'model_fallback': {
        // A8 — the active profile hit a persistent rate limit and the
        // backend retried once on its declared fallback model. Previously
        // silently DROPPED by this switch's missing `default` — the turn
        // would just finish on a different model with no visible reason.
        const d = _frame_data<ModelFallbackFrame>(frame)
        appendMessage({
          role: 'system',
          content: `${d.from_model} → ${d.to_model} (${d.reason.replace(/_/g, ' ')})`,
        })
        break
      }
      case 'tool_preparing': {
        // Model opened a tool_use block; args may still be streaming (and are
        // not shown as tokens). Without this the UI looks stuck after prose.
        const d = _frame_data<{ tool_name: string; tool_use_id: string }>(frame)
        appendMessage({
          role: 'tool',
          content: `… preparing ${d.tool_name}`,
          tool_use_id: d.tool_use_id, tool_name: d.tool_name,
        })
        break
      }
      case 'tool_request': {
        const d = _frame_data<{ tool_name: string; tool_use_id: string; safety_tier?: string }>(frame)
        // Remember the tier for the completion frame — `tool_result` doesn't
        // carry it. Consumed (and cleared) by tool_result / tool_error below.
        if (d.tool_use_id) toolTierRef.current.set(d.tool_use_id, d.safety_tier ?? '')
        appendMessage({
          role: 'tool',
          content: `→ ${d.tool_name}`,
          tool_use_id: d.tool_use_id, tool_name: d.tool_name,
        })
        break
      }
      case 'tool_pending_confirmation': {
        const d = _frame_data<ToolPendingFrame>(frame)
        setPending({
          tool_use_id: d.tool_use_id,
          tool_name: d.tool_name,
          args: d.args,
          safety_tier: d.safety_tier,
          confirmation_token: d.confirmation_token,
          ttl_seconds: d.ttl_seconds,
          expires_at_epoch_ms: Date.now() + d.ttl_seconds * 1000,
        })
        break
      }
      case 'tool_progress': {
        const d = _frame_data<ToolProgressFrame>(frame)
        if (d.tool_use_id) {
          appendToolProgress(d.tool_use_id, { kind: d.kind, line: d.line })
        }
        break
      }
      case 'tool_result': {
        const d = _frame_data<{ tool_name: string; tool_use_id: string }>(frame)
        // The chat-staleness fix: a completed tool whose tier is not `read`
        // may have changed any component, and the caches MUST follow — a
        // stale row here is spread into the user's next manual PUT and the
        // backend's remove+add cycle silently reverts the agent's work.
        // Tier-keyed blanket per ruling 2 (asset-write-chokepoint plan); an
        // unseen tool_use_id resolves to undefined and isMutatingTier fails
        // SAFE (a spurious refetch beats a silent revert).
        {
          const tier = toolTierRef.current.get(d.tool_use_id)
          toolTierRef.current.delete(d.tool_use_id)
          if (isMutatingTier(tier)) {
            invalidateAssetQueries(qc, useUIStore.getState().currentProject)
          }
        }
        appendMessage({
          role: 'tool',
          content: `✓ ${d.tool_name}`,
          tool_use_id: d.tool_use_id, tool_name: d.tool_name,
        })
        break
      }
      case 'tool_error': {
        const d = _frame_data<ToolErrorFrame>(frame)
        // Same invalidation as tool_result: a FAILED mutating tool may have
        // partially applied before raising, and serving the pre-attempt cache
        // as truth is the same staleness this fix exists to close.
        if (d.tool_use_id) {
          const tier = toolTierRef.current.get(d.tool_use_id)
          toolTierRef.current.delete(d.tool_use_id)
          if (isMutatingTier(tier)) {
            invalidateAssetQueries(qc, useUIStore.getState().currentProject)
          }
        }
        // v4-MAJOR-1 / v4-MINOR-1 / v6-F1 + Phase D upload errors — route
        // structured error_kinds into the ErrorBanner so the user sees a
        // typed banner instead of a gray tool-line buried in the message list.
        if (TOOL_ERROR_BANNER_KINDS.has(d.error_kind)) {
          setError({ error_kind: d.error_kind, message: d.message })
        }
        {
          // Generic tool_error used to show only the kind label, which hid the
          // real reason (e.g. Pydantic "bus Field required" on partial updates).
          const detail = (d.message || '').trim()
          const short =
            detail.length > 160 ? `${detail.slice(0, 157)}…` : detail
          appendMessage({
            role: 'tool',
            content: short
              ? `✗ ${d.tool_name ?? '?'} — ${d.error_kind}: ${short}`
              : `✗ ${d.tool_name ?? '?'} — ${d.error_kind}`,
            tool_use_id: d.tool_use_id, tool_name: d.tool_name,
          })
        }
        break
      }
      case 'ui_event': {
        const d = _frame_data<{
          kind?: string
          panel_id?: string
          results_tab?: string
          bottom_tab?: string
          compare_rail?: boolean
          compare_a?: string
          compare_b?: string
          compare_tab?: string
          component_class?: string
          name?: string
          snapshot_iso?: string
          period?: number | null
          category?: string
          metrics?: string[]
          mode?: 'chronological' | 'duration' | 'monthly'
          chart?: boolean
        }>(frame)
        try {
          applyUiNavigate(d)
        } catch (e) {
          console.warn('ui_event apply failed', e)
        }
        break
      }
      case 'project_rebound': {
        // The agent dispatched a tool that legitimately changed the
        // backend's active project (activate_project / load_project /
        // save_project_as / rename_project / restore_project_snapshot).
        // Mirror the change into uiStore.currentProject so the autosave
        // loop's `expect=<name>` matches the backend's binding —
        // otherwise the next autosave 409s with "Backend network is
        // bound to project X, not Y" (incident 2026-06-08).
        // Also covers unbound → open: set name + refresh lifecycle roots
        // so the canvas / StatusBar pick up the newly activated project
        // (backend already activated; do NOT call switchToProject again).
        const d = _frame_data<{ from: string | null; to: string | null; via_tool: string }>(frame)
        if (d.to && d.to !== useUIStore.getState().currentProject) {
          const ui = useUIStore.getState()
          ui.setCurrentProject(d.to)
          ui.setProjectName(d.to)
          ui.touchTab(d.to)
          qc.invalidateQueries({ queryKey: nk(d.to, 'meta') })
          qc.invalidateQueries({ queryKey: nk(d.to, 'simulationStatus') })
          qc.invalidateQueries({ queryKey: nk(d.to, 'snapshots') })
          toast(`Active project: ${d.to}`, { icon: '🔀' })
        }
        // Render a small tool-line so the conversation explains what
        // happened.
        appendMessage({
          role: 'tool',
          content: `🔀 active project: ${d.from ?? '(unbound)'} → ${d.to ?? '(unbound)'}`,
        })
        break
      }
      case 'turn_done': {
        // Modal reciprocity: a turn begun with the microphone is answered
        // aloud. Decided by `voiceTurnRef`, captured at SEND — `dictatedRef`
        // is cleared by the send itself, and the mute is read live so
        // muting mid-turn takes effect on this answer rather than the next.
        if (voiceTurnRef.current && useUIStore.getState().assistantSpeakEnabled) {
          const last = useChatStore.getState().messages
            .filter((m) => m.role === 'assistant').slice(-1)[0]
          if (last) speechOut.speak(speechOut.plainTextForSpeech(last.content))
        }
        voiceTurnRef.current = false
        const d = _frame_data<TurnDoneFrame>(frame)
        if (d.usage) {
          // M10: server reports token counts; client renders them as-is.
          accrueUsage({
            input_tokens: d.usage.input_tokens ?? 0,
            output_tokens: d.usage.output_tokens ?? 0,
            cache_read_tokens: d.usage.cache_read_tokens ?? 0,
            cache_create_tokens: d.usage.cache_create_tokens ?? 0,
          })
        }
        closeStream()
        break
      }
      case 'session_done': {
        closeStream()
        break
      }
      case 'error': {
        const d = _frame_data<{ error_kind: string; message: string }>(frame)
        setError({ error_kind: d.error_kind, message: d.message })
        closeStream()
        break
      }
    }
  }, [qc, setSessionId, appendMessage, appendTokenDelta, appendThinkingDelta, setPending,
      appendToolProgress, accrueUsage, setStreaming, setError, closeStream])

  // Phase D polish #3 — auto-uncheck-after-send opt-in setting.
  // Stored in localStorage; OFF by default (matches sticky-chip intent).
  // Toggled via the ⚙ gear popover in the header.
  const [autoUncheckAfterSend, setAutoUncheckAfterSend] = useState<boolean>(
    () => readPref('chat:autoUncheckAfterSend') === '1',
  )
  useEffect(() => {
    writePref('chat:autoUncheckAfterSend', autoUncheckAfterSend ? '1' : '0')
  }, [autoUncheckAfterSend])
  const [gearOpen, setGearOpen] = useState(false)

  // Phase D polish #3 — pending-send modal. When the user is about to
  // send a message with ≥1 attachment AND they've never confirmed before
  // (localStorage[chat:firstSendAck] !== '1'), surface a confirm modal
  // so the default-ON behaviour doesn't ambush them.
  const [pendingSendText, setPendingSendText] = useState<string | null>(null)
  const [pendingSendAttachIds, setPendingSendAttachIds] = useState<string[]>([])

  const dispatchSend = useCallback((text: string, attachIds: string[]) => {
    // FIRST, before the composer reset four lines below clears `dictatedRef`.
    // Reading it later — say, next to the createChatStream call that consumes
    // `input_mode` — always yields false, and the bug is invisible: the
    // request still carries the right mode, because that expression is
    // evaluated before the reset too. Only the SPOKEN answer goes missing.
    voiceTurnRef.current = dictatedRef.current
    appendMessage({
      role: 'user', content: text,
      attachment_file_ids: attachIds.length > 0 ? attachIds : undefined,
    })
    setInput('')
    dictatedRef.current = false
    setStreaming(true)
    setError(null)
    const cleanup = createChatStream(
      {
        session_id: sessionId ?? undefined,
        message: text,
        // Task 13 — `profile_id` is included ONLY when the user actually
        // picked one. `profileId === null` means "the server's active
        // profile", and OMITTING the field (never sending `model` either) is
        // how that stays true turn after turn — sending a selector every
        // time would re-assert a stale choice over an admin's
        // `set_active_profile` or an A8 rate-limit fallback.
        ...(profileId !== null ? { profile_id: profileId } : {}),
        attachment_file_ids: attachIds.length > 0 ? attachIds : undefined,
        // Built HERE, at send, not captured at mount or on a store
        // subscription: the user opens Results, selects a generator, and only
        // then asks. A context frozen earlier describes the screen they had
        // before they went looking, which is worse than no context at all —
        // it is a confident wrong referent.
        ui_context: buildUiContext() ?? undefined,
        // `voiceTurnRef`, not `dictatedRef`: this literal is evaluated
        // after dispatchSend has already reset the composer, so reading
        // the composer flag here always yields 'text'.
        input_mode: voiceTurnRef.current ? 'voice' : 'text',
      },
      handleFrame,
      (err) => {
        toast.error(`chat: connection lost — ${(err as Error).message ?? err}`)
        setStreaming(false)
      },
    )
    setStreamCleanup(cleanup)
    if (autoUncheckAfterSend && attachIds.length > 0) {
      setAttachedFileIds([])
    }
  }, [appendMessage, sessionId, profileId, handleFrame, setStreaming, setError,
      setStreamCleanup, autoUncheckAfterSend, setAttachedFileIds])

  const onSend = useCallback(() => {
    const text = input.trim()
    if (!text || streaming) return
    const attachIds = useChatStore.getState().attachedFileIds.slice()
    // First-send confirmation modal (default-ON friction killer).
    const firstAck = readPref('chat:firstSendAck') === '1'
    if (attachIds.length > 0 && !firstAck) {
      setPendingSendText(text)
      setPendingSendAttachIds(attachIds)
      return
    }
    dispatchSend(text, attachIds)
  }, [input, streaming, dispatchSend])

  const confirmSendWithAttachments = useCallback(() => {
    if (pendingSendText == null) return
    writePref('chat:firstSendAck', '1')
    dispatchSend(pendingSendText, pendingSendAttachIds)
    setPendingSendText(null)
    setPendingSendAttachIds([])
  }, [pendingSendText, pendingSendAttachIds, dispatchSend])

  const confirmSendWithoutFiles = useCallback(() => {
    if (pendingSendText == null) return
    writePref('chat:firstSendAck', '1')
    setAttachedFileIds([])
    dispatchSend(pendingSendText, [])
    setPendingSendText(null)
    setPendingSendAttachIds([])
  }, [pendingSendText, dispatchSend, setAttachedFileIds])

  const cancelPendingSend = useCallback(() => {
    setPendingSendText(null)
    setPendingSendAttachIds([])
  }, [])

  const onAbort = useCallback(async () => {
    // Stopping a turn has to stop the VOICE as well. A synthesiser that keeps
    // reading an answer the user just cancelled is the single most alarming
    // way this feature can fail — there is no visible progress bar to explain
    // why the machine is still talking.
    speechOut.cancelSpeech()
    voiceTurnRef.current = false
    if (!sessionId) return
    try {
      await postChatAbort(sessionId)
    } catch { /* idempotent */ }
    setStreaming(false)
  }, [sessionId, setStreaming])

  // SSE cleanup on unmount (CLAUDE.md rule) — but NOT while a turn is running.
  //
  // This panel is now mounted for the app's lifetime inside `AssistantDock`,
  // which renders it unconditionally and hides it with CSS when collapsed. So
  // the case that motivated this guard is gone: the panel answering a
  // `ui_event` by calling `setSlidePanel('results')` no longer unmounts
  // itself, because it does not live in the SlidePanel slot anymore, and
  // collapsing the dock does not unmount it either.
  //
  // The guard stays because unmount paths that still exist are exactly the
  // ones a mid-turn stream can hit: the dock's ErrorBoundary swapping in its
  // fallback after a render crash, a project switch or route change that tears
  // down the workbench tree, and HMR in dev. On any of those, closing a live
  // connection would leave the backend generating into a socket nobody is
  // reading — the reported "still streaming, no tokens on screen".
  //
  // Leaving it open is safe because `handleFrame` writes only to Zustand and
  // the query cache — never to this component's state — so the rest of the
  // turn lands correctly with the panel unmounted and renders when it comes
  // back. The connection is closed by `closeStream()` on the terminal frame,
  // by `onAbort`, and by `resetForProjectSwitch` — all of which outlive the
  // panel. An idle stream is still closed here.
  useEffect(() => {
    return () => {
      const { streaming, streamCleanup } = useChatStore.getState()
      if (streaming) return
      try { streamCleanup?.() } catch { /* idempotent */ }
    }
  }, [])

  // ── per-message actions ───────────────────────────────────────────────
  //
  // Retry and edit share one move: withdraw a turn from BOTH histories, then
  // do something with the question. `postChatRewind` is the server half and
  // is awaited first — re-sending before it lands would race the rewind
  // against the turn it is clearing space for, and the model would answer with
  // the discarded exchange still in context.
  const withdrawTurn = useCallback(async (userIdx: number) => {
    const sid = useChatStore.getState().sessionId
    if (sid) {
      try {
        await postChatRewind(sid, 1)
      } catch {
        // A failed rewind means the model would still see the old exchange.
        // Say so rather than proceeding into a retry that quietly repeats
        // itself — the silent version is what makes retry look broken.
        toast.error('Could not rewind the conversation — try again in a moment.')
        return false
      }
    }
    useChatStore.setState((st) => ({
      messages: st.messages.slice(0, userIdx),
      toolProgress: {},
      error: null,
    }))
    return true
  }, [])

  const onCopyMessage = useCallback((m: ChatMessage) => {
    // The raw markdown, not the rendered text: a copied answer is usually
    // pasted somewhere that renders markdown (a PR, a doc, an issue), where
    // the rendered form arrives as flattened prose with the table gone.
    navigator.clipboard?.writeText(m.content)
      .then(() => toast.success('Copied'))
      .catch(() => toast.error('Could not copy'))
  }, [])

  /** The user turn an assistant message is answering. */
  const precedingUserIndex = useCallback((id: string) => {
    const msgs = useChatStore.getState().messages
    const at = msgs.findIndex((x) => x.id === id)
    if (at < 0) return -1
    for (let i = at; i >= 0; i--) if (msgs[i].role === 'user') return i
    return -1
  }, [])

  const onRetryMessage = useCallback(async (m: ChatMessage) => {
    const idx = precedingUserIndex(m.id)
    if (idx < 0) return
    const question = useChatStore.getState().messages[idx]
    const attachIds = question.attachment_file_ids ?? []
    // Withdraw the ANSWER, keeping the question: it is being re-asked, not
    // retracted. dispatchSend re-appends it, so cut at the question's index.
    if (!await withdrawTurn(idx)) return
    dispatchSend(question.content, attachIds)
  }, [precedingUserIndex, withdrawTurn, dispatchSend])

  const onEditMessage = useCallback(async (m: ChatMessage) => {
    const idx = useChatStore.getState().messages.findIndex((x) => x.id === m.id)
    if (idx < 0) return
    const text = m.content
    // The files come back with the text. Retry always carried them; Edit did
    // not, so rewording a question about an attached PDF silently re-sent it
    // with no PDF — and the chips were gone from the composer too, so there
    // was nothing on screen to notice. Always assigned, never merged: a
    // leftover from a previous compose must not ride along with a question
    // that never mentioned it.
    const files = m.attachment_file_ids ?? []
    // The whole turn goes, question included — the user is replacing it, and
    // leaving the old phrasing above the new one would show them asking twice.
    if (!await withdrawTurn(idx)) return
    setInput(text)
    useChatStore.getState().setAttachedFileIds(files)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }, [withdrawTurn])

  /**
   * Re-ask the last question after a turn FAILED.
   *
   * Retry hangs off an assistant message, and a turn that dies before its
   * first token leaves none — so the case retry exists for was the one case
   * it did not cover. The rewind is not optional: `run_turn` appends the user
   * message to the server history before it calls the model, so an error does
   * not unwind it, and re-asking without rewinding stacks the question twice.
   */
  const onRetryLastTurn = useCallback(async () => {
    const msgs = useChatStore.getState().messages
    let idx = -1
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { idx = i; break }
    }
    if (idx < 0) return
    const question = msgs[idx]
    if (!await withdrawTurn(idx)) return
    dispatchSend(question.content, question.attachment_file_ids ?? [])
  }, [withdrawTurn, dispatchSend])

  // ⌘/Ctrl-J toggles the assistant, and the OPEN path lands the caret in the
  // composer.
  //
  // Bound here rather than in App.tsx's global handler because this component
  // owns `textareaRef` — and a shortcut that opens the panel but leaves the
  // caret elsewhere has saved nothing, which is the failure the focus effect
  // below exists to prevent.
  //
  // Deliberately NOT guarded on an editable target, unlike the palette's
  // ⌘K/⌘P: the composer IS an editable target, and "close the assistant I am
  // typing in" is the most natural moment to press this. The modifier check
  // still comes first, so a literal "j" never toggles a panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      if (e.key !== 'j' && e.key !== 'J') return
      e.preventDefault()
      const ui = useUIStore.getState()
      ui.setAssistantDockOpen(!ui.assistantDockOpen)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Focus follows the OPENING, never the mount. The dock defaults to open, so
  // focusing on mount would steal the caret on every page load — out of the
  // project search, out of a half-filled form, out of the canvas. The ref
  // starts at the CURRENT value so the first run after mount is a no-op.
  const prevDockOpenRef = useRef(assistantDockOpen)
  useEffect(() => {
    const opened = assistantDockOpen && !prevDockOpenRef.current
    prevDockOpenRef.current = assistantDockOpen
    if (!opened) return
    requestAnimationFrame(() => textareaRef.current?.focus())
  }, [assistantDockOpen])

  const onClearHistory = useCallback(() => {
    // Clear UI state in-place (does NOT trigger the project-switch reset
    // path, and does NOT null sessionId or call startNewChat — the server
    // session and its profile binding are unaffected). To wipe the on-disk
    // chat.jsonl too, the user invokes the clear_chat_history tool through
    // the agent.
    if (!confirm('Clear the conversation view? (On-disk chat.jsonl is untouched — ask the agent to clear_chat_history to wipe disk.)')) return
    useChatStore.setState({
      messages: [], pending: null, toolProgress: {}, error: null,
    })
  }, [])

  // Fix round 1 (product gap) — `startNewChat()` was reachable ONLY from the
  // cross-wire profile-switch confirm. A deployment where every configured
  // profile shares one wire had NO path at all to a fresh session short of
  // switching projects. This is the deliberate affordance for it — same
  // action the cross-wire confirm's "Switch" button takes, just without a
  // profile change attached.
  const onNewChat = useCallback(() => {
    startNewChat()
  }, [startNewChat])

  // ── Task 13 — profile dropdown + cross-wire switch confirm ───────────────
  //
  // `getChatProfiles()` is member-level (every authenticated user may read
  // which profiles exist), so the query itself needs no gating — but per
  // ADR-0001 (unresolvable data ships as a distinct state, never silently
  // reinterpreted as "empty") a REFUSED fetch must render differently from a
  // resolved-but-empty list. Three states before "ready", all disabled:
  // loading (`!profilesQuery.data`), refused (`profilesQuery.isError`), and
  // empty (`data.profiles.length === 0`).
  const profilesQuery = useChatProfiles()
  const chatProfiles = profilesQuery.data?.profiles ?? []
  const activeProfileId = profilesQuery.data?.active_profile_id ?? null
  // `profileId` (the store's explicit pick) wins; `null` falls back to
  // whatever the server currently has active. Both are real profile ids from
  // the SAME fetch, so this never lands on an id absent from `chatProfiles`
  // except in the brief window before the fetch resolves — handled by the
  // disabled placeholder below rather than by this fallback.
  const selectedProfileId = profileId ?? activeProfileId
  const selectedProfileMeta = chatProfiles.find((p) => p.id === selectedProfileId) ?? null

  const [pendingProfilePick, setPendingProfilePick] = useState<{ id: string; label: string } | null>(null)

  const onPickProfile = useCallback((id: string) => {
    const target = chatProfiles.find((p) => p.id === id)
    if (!target) return
    // Cross-wire (anthropic ⇄ openai) profiles do not share a session the
    // way two profiles on the same wire can — the confirm exists because
    // picking one silently mid-conversation would otherwise look like the
    // same assistant continuing when the backend has actually started over.
    //
    // Fix round 1 — FAIL SAFE when the current selection's wire is UNKNOWN
    // (`selectedProfileMeta === null`, e.g. an admin deleted the profile
    // `profileId` still points at). The old guard required a known
    // same-wire baseline to trigger the confirm, so an unknown baseline
    // short-circuited straight to `setProfileId` — a silent wire change
    // wearing the same-wire path. Treat "cannot prove it's same-wire" as
    // cross-wire: one extra confirm click costs less than a session
    // continuing under a provider it never agreed to switch to.
    if (!selectedProfileMeta || selectedProfileMeta.wire !== target.wire) {
      setPendingProfilePick({ id: target.id, label: target.label })
      return
    }
    setProfileId(id)
  }, [chatProfiles, selectedProfileMeta, setProfileId])

  const confirmProfileSwitch = useCallback(() => {
    if (!pendingProfilePick) return
    setProfileId(pendingProfilePick.id)
    startNewChat()
    setPendingProfilePick(null)
  }, [pendingProfilePick, setProfileId, startNewChat])

  const cancelProfileSwitch = useCallback(() => setPendingProfilePick(null), [])

  return (
    <div
      className="flex flex-col h-full overflow-hidden bg-bg relative"
      data-testid="chat-panel"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragActive && (
        <div
          className="absolute inset-0 z-50 bg-accent/15 border-[3px] border-dashed border-accent rounded-md flex items-center justify-center pointer-events-none"
          data-testid="chat-drop-overlay"
        >
          <div className="text-accent text-sm font-medium px-4 py-2 bg-bg-2 rounded shadow">
            Drop to upload — Excel, PDF, images (≤ 25 MB each)
          </div>
        </div>
      )}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        onChange={onFileInputChange}
        data-testid="chat-file-input"
      />
      <div className="flex items-center gap-2 px-3 h-8 border-b border-border bg-bg-2 shrink-0">
        {profilesQuery.isError ? (
          // ADR-0001 — a REFUSED fetch, never rendered as "no models
          // configured": that text means the server was reachable and said
          // "zero profiles exist", a materially different fact from
          // "couldn't find out".
          <select
            disabled
            data-profiles-state="error"
            className="max-w-[9rem] truncate bg-bg border border-border rounded px-1 py-0.5 text-[10px] text-danger"
            title="Could not load models — check your connection and try again"
            data-testid="chat-model-select"
          >
            <option>Could not load models</option>
          </select>
        ) : !profilesQuery.data ? (
          <select
            disabled
            data-profiles-state="loading"
            className="max-w-[9rem] truncate bg-bg border border-border rounded px-1 py-0.5 text-[10px] text-muted"
            data-testid="chat-model-select"
          >
            <option>Loading models…</option>
          </select>
        ) : chatProfiles.length === 0 ? (
          <select
            disabled
            data-profiles-state="empty"
            className="max-w-[9rem] truncate bg-bg border border-border rounded px-1 py-0.5 text-[10px] text-muted"
            title="No profiles are configured — ask an administrator to add one in Settings"
            data-testid="chat-model-select"
          >
            <option>No models configured</option>
          </select>
        ) : (
          <select
            value={selectedProfileId ?? ''}
            onChange={(e) => onPickProfile(e.target.value)}
            disabled={streaming}
            data-profiles-state="ready"
            // The dock is 380px and a native <select> sizes its closed box to
            // its widest option — a long profile label would otherwise widen
            // the whole header row. `truncate` + `title` keep the full label
            // reachable on hover without that.
            className="max-w-[9rem] truncate bg-bg border border-border rounded px-1 py-0.5 text-[10px]"
            title={selectedProfileMeta?.label ?? ''}
            data-testid="chat-model-select"
          >
            {chatProfiles.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        )}
        <UsageMeter />
        {/* Global mute for spoken answers. Beside the gear rather than inside
            it: the spec pairs reciprocity with "a global mute", and a mute
            you have to open a popover to reach is not one you can hit while
            the machine is mid-sentence. Hidden entirely where the platform has
            no speech synthesis — a dead toggle is worse than no toggle. */}
        {speechOut.isSpeechOutAvailable() && (
          <button
            className="px-1.5 py-0.5 text-[10px] rounded bg-bg-2 hover:bg-bg-3 border border-border"
            style={{ color: assistantSpeakEnabled ? 'var(--color-accent)' : 'var(--color-muted)' }}
            onClick={() => { if (assistantSpeakEnabled) speechOut.cancelSpeech(); toggleAssistantSpeak() }}
            title={assistantSpeakEnabled
              ? 'Spoken answers are on for dictated questions — click to mute'
              : 'Spoken answers are muted — click to unmute'}
            aria-label="Mute spoken answers"
            aria-pressed={!assistantSpeakEnabled}
            data-testid="chat-speak-toggle"
          >
            {assistantSpeakEnabled ? '🔊' : '🔇'}
          </button>
        )}
        {/* Phase D polish #3 — ⚙ gear popover for chat-panel preferences.
            Currently holds one toggle (auto-uncheck after send); future
            settings live here too. */}
        <div className="relative">
          <button
            className="px-1.5 py-0.5 text-[10px] rounded bg-bg-2 hover:bg-bg-3 border border-border text-muted"
            onClick={() => setGearOpen((v) => !v)}
            title="Chat panel settings"
            aria-label="Chat panel settings"
            aria-expanded={gearOpen}
            data-testid="chat-gear"
          >
            ⚙
          </button>
          {gearOpen && (
            <div
              className="absolute top-full left-0 mt-1 z-[100] w-64 bg-bg border border-border rounded shadow-lg p-2 text-[11px]"
              data-testid="chat-gear-popover"
            >
              <label className="flex items-start gap-2 cursor-pointer hover:bg-bg-2/40 rounded px-1 py-1">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-accent"
                  checked={autoUncheckAfterSend}
                  onChange={(e) => setAutoUncheckAfterSend(e.target.checked)}
                />
                <span>
                  <span className="text-text font-medium">Auto-uncheck after send</span>
                  <span className="block text-muted text-[10px] mt-0.5">
                    Attachments stay in the project, but their checkboxes
                    clear after each send so the next message starts fresh.
                  </span>
                </span>
              </label>
            </div>
          )}
        </div>
        {unseenExportCount > 0 && (
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-accent/20 hover:bg-accent/30 border border-accent/50 text-accent"
            onClick={clearUnseenExports}
            title="New files generated by the agent — click to acknowledge. Click the file's ↓ to download."
            data-testid="chat-exports-badge"
          >
            New exports ({unseenExportCount})
          </button>
        )}
        {/* Fix round 1 — the only other path to `startNewChat()` was the
            cross-wire confirm's "Switch" button, which a same-wire-only
            deployment never surfaces. `title`/`aria-label` carry the meaning
            so a compact icon button fits the 380px dock header alongside the
            dropdown, UsageMeter, gear, and exports badge without widening
            the row. */}
        <button
          className="ml-auto px-1.5 py-0.5 text-[10px] rounded bg-bg-2 hover:bg-bg-3 border border-border"
          onClick={onNewChat}
          disabled={streaming}
          data-testid="chat-new-chat"
          title="Start a new chat (new session; the on-screen conversation and current profile binding reset)"
          aria-label="Start a new chat"
        >
          🆕
        </button>
        <button
          className="px-2 py-0.5 text-[10px] rounded bg-bg-2 hover:bg-bg-3 border border-border"
          onClick={onClearHistory}
          disabled={streaming || messages.length === 0}
          data-testid="chat-clear-history"
          title="Clear the on-screen conversation. The on-disk chat.jsonl is untouched."
        >
          Clear
        </button>
        {streaming && (
          <button
            className="px-2 py-0.5 text-[10px] rounded bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/50"
            onClick={onAbort}
            data-testid="chat-abort"
          >
            Stop
          </button>
        )}
      </div>
      {/* Task 13 — cross-wire profile switch confirm. Inline rather than a
          modal: it interrupts nothing (the dropdown pick already committed
          nothing) and the whole decision fits in one sentence. */}
      {pendingProfilePick && (
        <div
          className="flex items-center gap-2 px-3 py-1 text-[11px] bg-accent/10 border-b border-accent/30 text-text shrink-0"
          data-testid="chat-profile-switch-confirm"
        >
          <span>Switching to {pendingProfilePick.label} starts a new chat</span>
          <button
            className="px-2 py-0.5 rounded bg-accent text-bg hover:opacity-90"
            onClick={confirmProfileSwitch}
            data-testid="chat-profile-switch-confirm-btn"
          >
            Switch
          </button>
          <button
            className="px-2 py-0.5 rounded bg-bg border border-border hover:bg-bg-3"
            onClick={cancelProfileSwitch}
            data-testid="chat-profile-switch-cancel-btn"
          >
            Cancel
          </button>
        </div>
      )}
      <ErrorBanner
        onRetry={onRetryLastTurn}
        activeProfileLabel={selectedProfileMeta?.label ?? null}
        sessionProfile={
          selectedProfileMeta
            ? { id: selectedProfileMeta.id, label: selectedProfileMeta.label }
            : null
        }
      />
      {historyGap > 0 && (
        <div
          role="status"
          className="border-l-2 border-amber-500 bg-amber-500/5 px-3 py-2 mx-3 my-2 text-xs"
          data-testid="chat-history-gap"
        >
          <div className="font-medium text-amber-400 mb-0.5">
            {historyGap} earlier {historyGap === 1 ? 'message' : 'messages'} could not be read
          </div>
          <div className="text-muted">
            Part of this project&apos;s saved conversation is damaged and has been
            skipped. What you see below is incomplete.
          </div>
        </div>
      )}
      {interruptedTurn && (
        <div
          role="status"
          className="border-l-2 border-amber-500 bg-amber-500/5 px-3 py-2 mx-3 my-2 text-xs"
          data-testid="chat-interrupted-turn"
        >
          <div className="font-medium text-amber-400 mb-0.5">
            Your last message was interrupted
          </div>
          <div className="text-muted mb-2">
            It was never answered, so it is not part of the conversation below.
          </div>
          {/* Shown verbatim: this is the thing the user lost, and reading it
              is what lets them decide whether it is still worth sending. */}
          <blockquote className="border-l border-border pl-2 text-text whitespace-pre-wrap break-words">
            {interruptedTurn.user}
          </blockquote>
          <div className="flex items-center gap-2 mt-2">
            <button
              className="px-2 py-1 text-[11px] rounded bg-bg-2 hover:bg-bg-3 border border-border"
              onClick={() => {
                setInput(interruptedTurn.user)
                setInterruptedTurn(null)
              }}
              data-testid="chat-interrupted-restore"
            >
              Put it back in the composer
            </button>
            <button
              className="px-2 py-1 text-[11px] rounded text-muted hover:text-text"
              onClick={() => setInterruptedTurn(null)}
              data-testid="chat-interrupted-dismiss"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
      <div className="relative flex-1 min-h-0 flex flex-col">
        <div
          ref={messagesScrollRef}
          className="flex-1 min-h-0 overflow-y-auto"
          data-testid="chat-messages"
          onScroll={onMessagesScroll}
        >
        {/* The launch orientation (spec: "The launch orientation"). This was
            `ChatEmptyState`, gated on `!currentProject` — so it was invisible
            in exactly the case the spec cares most about, a project already
            open whose name, size and solve status the assistant should be
            able to state without being asked. The no-project variant is now
            one branch of it rather than the whole thing.

            Still gated on an empty conversation: a returning user replaying
            stale chat history is not being oriented, and a greeting pinned
            above a live conversation is a header repeating what they have
            moved past. */}
        {messages.length === 0 && <ChatLaunchGreeting />}
        {/* Discoverability chips: unbound → open/browse; bound → compare /
            navigate / summarize. Click fills the composer for edit-before-send. */}
        {messages.length === 0 && (
          <ChatStarterChips
            prompts={currentProject ? CHAT_STARTER_PROMPTS : CHAT_STARTER_PROMPTS_UNBOUND}
            disabled={streaming}
            onPick={(text) => {
              setInput(text)
              requestAnimationFrame(() => textareaRef.current?.focus())
            }}
          />
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={
              'px-3 py-1.5 text-[13px] leading-relaxed tracking-[-0.005em] ' +
              (m.role === 'user' ? 'text-text bg-bg-2/40 border-b border-border/40' :
               m.role === 'tool' ? 'text-muted font-mono text-[11px] tracking-normal leading-snug' :
               // Task 13 — model_fallback lines. Distinct from a plain
               // assistant bubble (italic, muted, no markdown) without going
               // as far as the tool row's monospace treatment.
               m.role === 'system' ? 'text-muted italic text-[11px] tracking-normal leading-snug' :
               'text-text')
            }
            data-role={m.role}
            data-testid="chat-message"
          >
            {/* Task 13 — accumulated `thinking` SSE deltas. Minimal collapsed-
                by-default block, above the answer since thinking precedes the
                model's text in the turn. Gated on presence, not truthiness of
                content, so a turn with no thinking block renders nothing. */}
            {m.role === 'assistant' && m.thinking && (
              <details className="mb-1 text-muted text-[11px]" data-testid="chat-thinking-block">
                <summary className="cursor-pointer select-none">Thinking</summary>
                <div className="whitespace-pre-wrap pt-1">{m.thinking}</div>
              </details>
            )}
            {/* Assistant replies are GitHub-flavored markdown (tables, bold,
                headers, lists) — render them. User/tool messages are plain
                text; keep their newlines with pre-wrap so multi-line input and
                tool tags aren't flattened. */}
            {m.role === 'assistant'
              ? <ChatMarkdown>{m.content}</ChatMarkdown>
              : <span className="whitespace-pre-wrap">{m.content}</span>}
            {m.role === 'tool' && m.tool_use_id && (
              <ToolProgressDetails toolUseId={m.tool_use_id} />
            )}
            {m.role === 'user' && m.attachment_file_ids && m.attachment_file_ids.length > 0 && (
              <ReplayAttachmentChips fileIds={m.attachment_file_ids} />
            )}
            <MessageActions message={m} streaming={streaming}
              onCopy={onCopyMessage} onRetry={onRetryMessage} onEdit={onEditMessage} />
          </div>
        ))}
        <ConfirmationCard />
        <div ref={messagesEndRef} />
        </div>
        {showJumpLatest && (
          <button
            type="button"
            className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10 px-2.5 py-1 text-[11px] rounded border border-border bg-bg-2/95 text-text shadow-sm hover:bg-bg-3"
            onClick={scrollToLatest}
            data-testid="chat-jump-latest"
          >
            ↓ Latest
          </button>
        )}
      </div>
      {/* Phase D polish #5 — touch-device fallback hint. Shows once on first
          coarse-pointer mount; auto-dismisses after 5 s OR on click. */}
      {touchHintVisible && (
        <button
          className="text-[11px] px-3 py-2 bg-accent/15 border-t border-accent/40 text-accent text-left hover:bg-accent/25 transition-colors shrink-0"
          onClick={dismissTouchHint}
          data-testid="chat-touch-hint"
        >
          💡 Tap 📎 to pick a file or 📋 to paste an image / PDF from your
          clipboard. (Drag-drop isn't available on touch devices — tap to
          dismiss.)
        </button>
      )}
      {/* Phase D — live upload chip strip. Hidden when no project loaded or
          no uploads exist. Each chip carries a checkbox controlling
          attach-to-next-message + a delete (trash) button. */}
      <UploadChipStrip
        uploads={uploads}
        attachedFileIds={attachedFileIds}
        toggleAttached={toggleAttached}
        onDelete={onDeleteChip}
        currentProject={currentProject}
      />
      <div
        className="flex flex-col border-t border-border bg-bg-2 shrink-0"
        style={{ height: promptHeight }}
        data-testid="chat-prompt-container"
      >
        <div
          onMouseDown={onDragStart}
          className="h-2 cursor-row-resize flex items-center justify-center hover:bg-bg-3/50 transition-colors group"
          title="Drag to resize the prompt"
          data-testid="chat-prompt-resize"
        >
          <div className="w-8 h-[2px] rounded-full bg-border group-hover:bg-accent/60 transition-colors" />
        </div>
        <div className="flex items-stretch gap-2 px-2 pb-2 flex-1 min-h-0">
          <div className="flex flex-col gap-1 self-end pb-0.5">
            {/* Phase D polish #5 — 44×44 px touch targets on coarse pointers
                (WCAG 2.5.5). Desktop stays compact. */}
            <button
              className={
                (isCoarsePointer
                  ? 'min-w-[44px] min-h-[44px] text-lg '
                  : 'px-2 py-1 text-[10px] ') +
                'rounded bg-bg-3/40 hover:bg-bg-3 border border-border text-muted'
              }
              onClick={onClickFilePicker}
              disabled={streaming || !currentProject}
              title={
                currentProject
                  ? 'Upload a file (Excel / PDF / image, ≤25 MB)'
                  : 'Load a project first'
              }
              aria-label="Upload a file"
              data-testid="chat-file-picker"
            >
              📎
            </button>
            <button
              className={
                (isCoarsePointer
                  ? 'min-w-[44px] min-h-[44px] text-lg '
                  : 'px-2 py-1 text-[10px] ') +
                'rounded bg-bg-3/40 hover:bg-bg-3 border border-border text-muted'
              }
              onClick={handlePasteFromClipboard}
              disabled={streaming || !currentProject}
              title={
                currentProject
                  ? 'Paste image or PDF from clipboard'
                  : 'Load a project first'
              }
              aria-label="Paste image or PDF from clipboard"
              data-testid="chat-paste-image"
            >
              📋
            </button>
            <button
              className={
                (isCoarsePointer
                  ? 'min-w-[44px] min-h-[44px] text-lg '
                  : 'px-2 py-1 text-[10px] ') +
                'rounded border ' +
                (speech.listening
                  ? 'bg-accent/15 border-accent text-accent'
                  : 'bg-bg-3/40 hover:bg-bg-3 border-border text-muted') +
                (speech.available && !streaming ? '' : ' opacity-50')
              }
              onClick={speech.toggle}
              disabled={!speech.available || streaming}
              title={
                !speech.supported
                  ? 'Voice input needs Chrome or Edge'
                  : speech.permissionDenied
                    ? 'Microphone access denied — allow it in System Settings → Privacy & Security → Microphone'
                    : speech.listening
                      ? 'Stop voice input (Esc)'
                      : 'Start voice input (English)'
              }
              aria-label={speech.listening ? 'Stop voice input' : 'Start voice input'}
              aria-pressed={speech.listening}
              data-testid="chat-mic"
            >
              {speech.listening ? '■' : '🎙'}
            </button>
          </div>
          <div className="flex-1 min-w-0 min-h-0 flex flex-col">
            <textarea
              ref={textareaRef}
              className="flex-1 min-h-0 bg-bg border border-border rounded px-2 py-1.5 text-[13px] leading-relaxed tracking-[-0.005em] resize-none focus:outline-none focus:border-accent/60"
              placeholder={streaming ? 'streaming…' : 'message…   (Shift+Enter for newline)'}
              value={input}
              onChange={(e) => { dictatedRef.current = false; setInput(e.target.value) }}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && speech.listening) {
                  // stopPropagation as well as preventDefault. App.tsx's
                  // window-level keydown handler also acts on Escape (close
                  // the compare rail, then the active slide panel), and
                  // preventDefault does NOT stop propagation — so without
                  // this the keystroke that stops the mic also closed the
                  // panel the agent had just opened. App.tsx now skips
                  // Escape for editable targets too; this is the near side of
                  // the same fix and keeps the behaviour correct on its own.
                  e.preventDefault()
                  e.stopPropagation()
                  speech.stop()
                  return
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  onSend()
                }
              }}
              disabled={streaming}
              data-testid="chat-input"
            />
            {speech.listening && (
              <div
                className="text-[11px] text-muted px-1 pt-0.5 truncate"
                data-testid="chat-speech-interim"
                aria-live="polite"
              >
                {speech.interim ? `…${speech.interim}` : 'Listening…'}
              </div>
            )}
          </div>
          <button
            className="self-end px-3 py-1.5 text-xs rounded bg-accent text-bg disabled:opacity-50 max-w-[260px]"
            onClick={onSend}
            disabled={streaming || !input.trim()}
            data-testid="chat-send"
            title={
              attachedFileIds.length > 0
                ? `Sending with ${attachedFileIds.length} file(s): ` +
                  attachedFileIds
                    .map((fid) => uploads.find((u) => u.file_id === fid)?.filename || fid)
                    .join(', ')
                : 'Send'
            }
          >
            {(() => {
              // Phase D polish #3 — show up to 2 filenames + "+N more"
              // instead of just the count, so the user sees what's
              // about to fly to the agent.
              if (attachedFileIds.length === 0) return 'Send'
              const names = attachedFileIds
                .map((fid) => uploads.find((u) => u.file_id === fid)?.filename)
                .filter((n): n is string => Boolean(n))
              if (names.length === 0) return `Send (📎${attachedFileIds.length})`
              if (names.length === 1) return `Send ▸ 📎 ${names[0]}`
              if (names.length === 2) return `Send ▸ 📎 ${names[0]} · ${names[1]}`
              return `Send ▸ 📎 ${names[0]} · ${names[1]} · +${names.length - 2} more`
            })()}
          </button>
        </div>
      </div>
      {/* Phase D polish #3 — first-send confirmation modal. Fires once
          per browser (localStorage[chat:firstSendAck]); after the user
          acknowledges the default-ON behaviour we never block again. */}
      {pendingSendText != null && (
        <div
          className="absolute inset-0 z-[200] bg-bg/80 backdrop-blur-sm flex items-center justify-center"
          data-testid="chat-first-send-modal"
          onClick={cancelPendingSend}
        >
          <div
            className="bg-bg-2 border border-border rounded-md p-4 max-w-md mx-4 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-medium text-text mb-2">
              Send with {pendingSendAttachIds.length} attachment{pendingSendAttachIds.length === 1 ? '' : 's'}?
            </div>
            <div className="text-[12px] text-muted mb-3 leading-relaxed">
              {'You\'re about to send '}
              <span className="italic">"{pendingSendText.length > 80 ? pendingSendText.slice(0, 80) + '…' : pendingSendText}"</span>
              {' with '}
              {pendingSendAttachIds.length === 1 ? 'this file' : `${pendingSendAttachIds.length} files`}{' attached:'}
            </div>
            <ul className="text-[11px] text-text mb-3 max-h-32 overflow-y-auto bg-bg rounded border border-border px-2 py-1">
              {pendingSendAttachIds.map((fid) => {
                const u = uploads.find((x) => x.file_id === fid)
                return (
                  <li key={fid} className="py-0.5">
                    {u ? `${_mimeIcon(u.mime)} ${u.filename}` : `📎 ${fid}`}
                  </li>
                )
              })}
            </ul>
            <div className="text-[10px] text-muted mb-3">
              This confirmation appears once per device. Toggle "Auto-uncheck
              after send" in the ⚙ menu if you'd rather review attachments
              before every send.
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                className="px-3 py-1.5 text-xs rounded bg-bg border border-border text-text hover:bg-bg-3/40"
                onClick={cancelPendingSend}
                data-testid="chat-first-send-cancel"
              >
                Cancel
              </button>
              <button
                className="px-3 py-1.5 text-xs rounded bg-bg-2 border border-border text-text hover:bg-bg-3/40"
                onClick={confirmSendWithoutFiles}
                data-testid="chat-first-send-without"
              >
                Send without files
              </button>
              <button
                className="px-3 py-1.5 text-xs rounded bg-accent text-bg hover:opacity-90"
                onClick={confirmSendWithAttachments}
                data-testid="chat-first-send-with"
              >
                Send with attachments
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
