import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'

import { postChatConfirm } from '../api/chat'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { mismatchSentence } from '../utils/projectMismatch'
import { EditCardTable, editCardOf } from './EditCard'

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

// P26 (carried from the P25 gate): in Guided every write-tier call asks for
// confirmation, but some write-tier tools do not edit the network — they
// write a file, back the project up, or open another project. The card says
// what the call is for; "Confirm this change" stays for real edits. Keyed on
// the tier as well, so a tool that ever moves tier falls back to the tier's
// wording. Expert never reads this.
const GUIDED_WRITE_PURPOSE: Record<string, { header: string; note: string }> = {}
for (const t of ['export_to_csv', 'export_to_excel', 'export_preview_png', 'export_chat_summary',
  'export_asset_results', 'gridspine_export_handoff_bundle']) {
  GUIDED_WRITE_PURPOSE[t] = { header: 'Confirm: export a file',
    note: 'Writes a file you can download. Your network is not changed.' }
}
// The decision study (U3): what each write changes, in the user's words.
GUIDED_WRITE_PURPOSE.start_decision_study = { header: 'Confirm: start a decision study',
  note: 'Creates a new project for the study. The project open now is not changed.' }
GUIDED_WRITE_PURPOSE.set_study_parameter = { header: 'Confirm: change an assumption',
  note: 'Changes one value in the study’s assumptions. The last result no longer matches until the study runs again.' }
GUIDED_WRITE_PURPOSE.generate_study_report = { header: 'Confirm: write the report',
  note: 'Writes the decision report as a file you can download. The study is not changed.' }
GUIDED_WRITE_PURPOSE.create_project_snapshot = { header: 'Confirm: save a copy',
  note: 'Saves a backup copy of the project as it is now. Your network is not changed.' }
for (const t of ['load_project', 'activate_project']) {
  GUIDED_WRITE_PURPOSE[t] = { header: 'Confirm: open a project',
    note: 'Switches the workbench to another project. Save first if you have unsaved edits.' }
}

function guidedConfirmWording(tool: string, tier: string): { header: string; note: string | null } {
  if (tier !== 'write') return { header: 'Confirm', note: null }
  return Object.prototype.hasOwnProperty.call(GUIDED_WRITE_PURPOSE, tool)
    ? GUIDED_WRITE_PURPOSE[tool]
    : { header: 'Confirm this change', note: null }
}

// P26 (coordinator item 5): a Guided card leads with one plain sentence
// about the call; the tool id and its JSON arguments move into a collapsed
// "Details". Unknown tools fall back to "The assistant wants to use <tool
// name in words>". Expert never reads this.
const _str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)
const _count = (v: unknown): number | null => (Array.isArray(v) ? v.length : null)
const GUIDED_CARD_SUMMARY: Record<string, (a: Record<string, unknown>) => string> = {
  run_eh_study: (a) => {
    const n = typeof a.budget_solves === 'number' && Number.isFinite(a.budget_solves) ? a.budget_solves : null
    return n != null
      ? `Run the reliability study for this site (about ${n} calculation steps)`
      : 'Run the reliability study for this site'
  },
  run_fmea_sweep: () => 'Run the equipment-failure check (FMEA)',
  // campus island I7: the loop re-solves and saves the hub and rewrites the campus file's sizes.
  campus_apply_island_cuts: (a) => `Apply the frequency cuts to ${_str(a.project_id) ?? 'this project'}: `
    + 're-solve and save the hub, update the sizes in its campus file and rerun the island check, up to 3 times',
  // GFM-b: the recommender is a campus job; the apply writes the sidecar and the campus file.
  campus_recommend_gfm_settings: (a) => `Recommend grid-forming settings for ${_str(a.project_id) ?? 'this project'} `
    + 'from its last campus run (heuristic screening)',
  campus_apply_gfm_settings: (a) => 'Write the recommended grid-forming settings into the island data and the '
    + `campus file of ${_str(a.project_id) ?? 'this project'}; the hub then needs a re-solve`,
  run_decision_study: (a) => a.stage === 'tornado'
    ? 'Test the study’s best option against each key assumption’s range'
    : 'Run the decision study: calculate every option',
  start_decision_study: (a) => `Start the decision study “${_str(a.name) ?? 'new study'}” in a project of its own`,
  set_study_parameter: (a) => {
    const v = typeof a.value === 'number' && Number.isFinite(a.value) ? a.value : null
    return `Set the assumption ${_str(a.key)?.replace(/_/g, ' ') ?? 'one assumption'}${v != null ? ` to ${v}${_str(a.unit) ? ` ${a.unit}` : ''}` : ''}`
  },
  generate_study_report: (a) => `Write the decision report (${_str(a.format) ?? 'docx'})`,
  run_simulation: () => 'Run a full calculation of the network',
  update_component: (a) => `Change the settings of ${_str(a.name) ?? 'one component'}`,
  bulk_update_components: (a) => {
    const n = _count(a.names)
    return n != null ? `Change the settings of ${n} components` : 'Change the settings of several components'
  },
  update_solver_config: (a) => {
    const p = (a.partial ?? {}) as Record<string, unknown>
    return typeof p.voll === 'number' && Number.isFinite(p.voll)
      ? `Set the price of undelivered energy to €${p.voll.toLocaleString('en-US')} per MWh`
      : 'Change the study settings'
  },
  put_stress_scenarios: (a) => {
    const n = _count(a.scenarios)
    return n != null ? `Save the list of hard conditions to test (${n} scenarios)`
      : 'Save the list of hard conditions to test'
  },
  create_project_snapshot: (a) => _str(a.label)
    ? `Save a backup copy named "${a.label}"` : 'Save a backup copy of the project',
  load_project: (a) => _str(a.name) ? `Open the project ${a.name}` : 'Open another project',
  activate_project: (a) => {
    const n = _str(a.project_id) ?? _str(a.name)
    return n ? `Open the project ${n}` : 'Open another project'
  },
  save_project: (a) => {
    const n = _str(a.name)
    const base = n ? `Save the project ${n}` : 'Save the project'
    return a.force === true ? `${base}, overwriting the saved version` : base
  },
  // P26 gate B2: every destructive and execution tool names its target
  // (BE/services/chat_tools_schema.py safety tiers), because the raw call is
  // no longer the first thing on the card.
  delete_component: (a) => `Delete ${_str(a.name) ?? 'a component'} from the network`,
  cascade_delete_bus: (a) => `Delete the bus ${_str(a.name) ?? '(unnamed)'} and everything connected to it`,
  batch_delete_components: (a) => {
    const names = Array.isArray(a.names) ? a.names.map(String) : []
    if (!names.length) return 'Delete several components from the network'
    const shown = names.slice(0, 5).join(', ')
    const rest = names.length - 5
    return `Delete ${names.length} components from the network: ${shown}${rest > 0 ? ` and ${rest} more` : ''}`
  },
  cluster_network: (a) => typeof a.n_clusters === 'number'
    ? `Simplify the network by merging buses into ${a.n_clusters} groups (replaces the current network)`
    : 'Simplify the network by merging buses (replaces the current network)',
  delete_vintage_bounds: (a) => `Remove the build-year limits of ${_str(a.name) ?? 'a component'}`,
  delete_timeseries: (a) => `Delete ${_str(a.attribute) ? `the ${a.attribute}` : 'a'} time series of ${
    _str(a.name) ?? 'a component'}`,
  run_ac_pf_stage: () => 'Run the power-flow check on the network',
  abort_simulation: () => 'Stop the running calculation',
  force_reset_simulation: () => 'Force-reset the calculation state (stops anything running)',
  run_frontier_study: () => 'Run the cost-versus-reliability check',
  run_mc_study: (a) => typeof a.draws === 'number'
    ? `Run the reliability simulation (${a.draws} runs)` : 'Run the reliability simulation',
  run_coupling_loop: (a) => typeof a.target_lole_h === 'number'
    ? `Size the design to meet ${a.target_lole_h} h/yr of shortfall (repeated calculations)`
    : 'Size the design to meet the shortfall goal (repeated calculations)',
  run_margin_loop: (a) => typeof a.target_lole_h === 'number'
    ? `Size the reserve margin to meet ${a.target_lole_h} h/yr of shortfall (repeated calculations)`
    : 'Size the reserve margin to meet the shortfall goal (repeated calculations)',
  abort_adequacy_study: (a) => `Stop the running ${_str(a.study) ?? 'reliability'} study`,
  solve_queue_enqueue: (a) => `Queue a calculation of the project ${_str(a.project_id) ?? '(current)'}`,
  solve_queue_abort: (a) => `Stop the queued calculation ${_str(a.job_id) ?? ''}`.trimEnd(),
  save_project_as: (a) => `Save the project under the new name ${_str(a.name) ?? '(unnamed)'}`,
  save_project_a_copy: (a) => `Save a copy of the project named ${_str(a.name) ?? '(unnamed)'}`,
  rename_project: (a) => `Rename the project ${_str(a.name) ?? '(unnamed)'} to ${_str(a.new_name) ?? '(unnamed)'}`,
  delete_project: (a) => `Delete the project ${_str(a.name) ?? '(unnamed)'}`
    + (a.cascade === true ? ' and every scenario made from it' : ''),
  create_scenario: (a) => `Create the scenario ${_str(a.new_name) ?? '(unnamed)'} from ${_str(a.base) ?? 'the project'}`,
  import_project_bundle: (a) => _str(a.filename)
    ? `Import a project from the file ${a.filename}` : 'Import a project from a file',
  create_project_from_template: (a) =>
    `Create the project ${_str(a.new_name) ?? '(unnamed)'} from the ${_str(a.template_id) ?? ''} template`,
  restore_project_snapshot: (a) =>
    `Restore the project ${_str(a.name) ?? '(unnamed)'} to the backup ${_str(a.snapshot_id) ?? ''} (replaces the current network)`,
  delete_project_snapshot: (a) =>
    `Delete the backup ${_str(a.snapshot_id) ?? ''} of the project ${_str(a.name) ?? '(unnamed)'}`,
  clear_audit_log: () => 'Clear the change log of this project',
  undo_last: () => 'Undo the last change to the network',
  clear_chat_history: () => "Delete this project's chat history",
  apply_demand_from_excel: (a) =>
    `Replace the demand of ${_str(a.load_name) ?? 'a load'} with values from the uploaded file ${_str(a.file_id) ?? ''}`.trimEnd(),
  delete_upload: (a) => `Delete the uploaded file ${_str(a.file_id) ?? ''}`.trimEnd(),
  reconstruct_network_from_image: (a) =>
    `Build the network from the uploaded image ${_str(a.file_id) ?? ''} (replaces the current network)`,
  clear_uploads: () => "Delete every uploaded file of this project",
  set_active_profile: (a) => `Switch the assistant to the model profile ${_str(a.profile_id) ?? ''}`.trimEnd(),
  gridspine_run_pipeline: (a) => `Run the GridSpine pipeline for the project ${_str(a.project_id) ?? '(current)'}`,
}
for (const t of ['import_network_nc', 'import_csv_bundle', 'import_excel', 'import_matpower']) {
  GUIDED_CARD_SUMMARY[t] = (a) => (_str(a.filename)
    ? `Replace the whole network with the imported file ${a.filename}`
    : 'Replace the whole network with an imported file')
}
// The fallback names the first identifying argument it finds, in this order.
const IDENTIFYING_ARGS = ['name', 'names', 'component', 'project_id', 'project', 'file_id', 'filename', 'path']
function _identifying(a: Record<string, unknown>): string | null {
  for (const k of IDENTIFYING_ARGS) {
    const v = a[k]
    if (typeof v === 'string' && v.trim()) return v
    if (Array.isArray(v) && v.length) return v.map(String).join(', ')
  }
  return null
}
for (const t of ['export_to_csv', 'export_to_excel', 'export_preview_png', 'export_chat_summary',
  'export_asset_results', 'gridspine_export_handoff_bundle']) {
  GUIDED_CARD_SUMMARY[t] = (a) => (_str(a.filename) ? `Export the file ${a.filename}` : 'Export a file')
}

export function guidedCardSummary(tool: string, args: Record<string, unknown>): string {
  const f = Object.prototype.hasOwnProperty.call(GUIDED_CARD_SUMMARY, tool) ? GUIDED_CARD_SUMMARY[tool] : null
  if (f) return f(args ?? {})
  const target = _identifying(args ?? {})
  return `The assistant wants to use ${tool.replace(/_/g, ' ')}${target ? ` on ${target}` : ''}`
}

type ReviewedChange = { component_class: string; name: string; attribute: string;
  before: string | number | boolean; after: string | number | boolean; unit: string }

function completePreview(args: Record<string, unknown> | undefined): ReviewedChange[] | null {
  if (!args || args.diff_truncated === true || typeof args.review_sha256 !== 'string'
    || !/^[a-f0-9]{64}$/.test(args.review_sha256)) return null
  const rows = args.reviewed_changes
  const scalar = (v: unknown) => typeof v === 'string' || typeof v === 'boolean'
    || (typeof v === 'number' && Number.isFinite(v))
  if (!Array.isArray(rows) || rows.length < 1 || rows.length > 20 || args.changes_total !== rows.length
    || !rows.every(r => r && typeof r === 'object'
      && ['component_class', 'name', 'attribute'].every(k => typeof r[k] === 'string' && r[k].length > 0)
      && typeof r.unit === 'string' && scalar(r.before) && scalar(r.after))) return null
  return rows as ReviewedChange[]
}

export function ConfirmationCard() {
  const pending = useChatStore((s) => s.pending)
  const assistantDockOpen = useUIStore((s) => s.assistantDockOpen)
  const setPending = useChatStore((s) => s.setPending)
  const setError = useChatStore((s) => s.setError)
  const sessionId = useChatStore((s) => s.sessionId)
  const appendMessage = useChatStore((s) => s.appendMessage)
  const uiMode = useUIStore((s) => s.uiMode)
  // P27b gate note 6: approving runs the write on the BACKEND's project — while
  // this tab and the backend disagree, Approve waits (Deny writes nothing).
  const projectMismatch = useUIStore((s) => s.projectMismatch)
  const mismatchLine = projectMismatch ? mismatchSentence(projectMismatch) : null
  const [secondsLeft, setSecondsLeft] = useState<number>(0)
  const [typedConfirmation, setTypedConfirmation] = useState<string>('')
  const timerRef = useRef<number | null>(null)
  const review = completePreview(pending?.args)
  const edit = editCardOf(pending?.edit_card)
  // LC-04: a chat edit's card that arrived malformed is never approvable.
  const reviewUnavailable = (pending?.tool_name === 'apply_project_changes' && !review)
    || (pending?.edit_card !== undefined && !edit)

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
        // Only this card: a newer one may already be showing (R1).
        if (useChatStore.getState().pending?.confirmation_token === pending.confirmation_token) {
          setPending(null)
        }
        // P25 re-gate note 1: a card left to lapse ends its card run like a
        // Deny — the rest of that click's queued actions are dropped.
        const lapsedGroup = useChatStore.getState().activeRequest?.group
        if (lapsedGroup) useChatStore.getState().dropRequestGroup(lapsedGroup)
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

  // P25 re-gate R1: several Guided writes in one response are carded one
  // after the other, and the NEXT card's SSE frame often lands while this
  // card's /confirm POST is still in flight. Clear only the card that was
  // answered — the token is captured before the await.
  const clearIfStill = useCallback((token: string) => {
    if (useChatStore.getState().pending?.confirmation_token === token) setPending(null)
  }, [setPending])

  const onApprove = useCallback(async () => {
    if (!pending || !sessionId) return
    if (reviewUnavailable) return
    if (useUIStore.getState().projectMismatch) return
    const token = pending.confirmation_token
    try {
      await postChatConfirm(sessionId, {
        token, decision: 'approve',
      })
      clearIfStill(token)
    } catch (err) {
      toast.error(`confirmation failed: ${(err as Error).message}`)
    }
  }, [pending, sessionId, clearIfStill, reviewUnavailable])

  const onDeny = useCallback(async () => {
    if (!pending || !sessionId) return
    const token = pending.confirmation_token
    try {
      await postChatConfirm(sessionId, {
        token, decision: 'deny',
      })
      // P25 gate: a denial ends that card's run — the card's remaining
      // queued actions (Improve: one message per action) are dropped.
      const group = useChatStore.getState().activeRequest?.group
      if (group) useChatStore.getState().dropRequestGroup(group)
      appendMessage({
        role: 'tool', content: `denied: ${pending.tool_name}`,
        tool_use_id: pending.tool_use_id, tool_name: pending.tool_name,
      })
      clearIfStill(token)
    } catch (err) {
      toast.error(`confirmation failed: ${(err as Error).message}`)
    }
  }, [pending, sessionId, clearIfStill, appendMessage])

  if (!pending) return null

  // Phase 4 — typed confirmation widget for the highest-risk tools.
  // v4-MAJOR-1 / v6-F1: save_project + save_project_as (force-overwrite path),
  // v4-MINOR-1: delete_project, restore_project_snapshot, cascade_delete_bus.
  const requiresTyped = TYPED_CONFIRMATION_TOOLS.has(pending.tool_name)
  const typedTarget = requiresTyped
    ? _typed_confirmation_target(pending.tool_name, pending.args)
    : null
  const typedSatisfied = !requiresTyped || (typedTarget != null && typedConfirmation === typedTarget)
  const guidedWording = uiMode === 'guided'
    ? guidedConfirmWording(pending.tool_name, pending.safety_tier)
    : null

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
      data-testid={assistantDockOpen ? 'chat-confirmation-card' : 'companion-confirmation'}
      data-tool-name={pending.tool_name}
      data-safety-tier={pending.safety_tier}
    >
      <div className={uiMode === 'guided'
        ? 'text-[12px] font-semibold text-amber-500 mb-1'
        : 'text-[11px] uppercase tracking-wider text-amber-500 mb-1'}
        data-testid="chat-confirmation-header">
        {/* P25 re-gate note 2 / P26: plain words in Guided; Expert unchanged. */}
        {guidedWording
          ? guidedWording.header
          : <>Confirm · {pending.safety_tier}</>}
      </div>
      {guidedWording?.note && (
        <div className="text-[11px] text-muted mb-1" data-testid="chat-confirmation-note">
          {guidedWording.note}
        </div>
      )}
      {guidedWording ? (
        <>
          <div id="chat-confirmation-title" className="text-sm font-medium mb-1 text-text"
            data-testid="chat-confirmation-summary">
            {edit?.says || guidedCardSummary(pending.tool_name, pending.args)}
          </div>
          {/* P26 gate B2: a destructive card shows its raw call by default. */}
          <details className="mb-2 text-[11px] text-muted" data-testid="chat-confirmation-details"
            open={pending.safety_tier === 'destructive'}>
            <summary className="cursor-pointer select-none">Details</summary>
            <div className="font-mono mt-1">{pending.tool_name}</div>
            <pre className="text-[10px] text-muted bg-bg-2 p-2 rounded overflow-x-auto mt-1 whitespace-pre-wrap break-all">
              {JSON.stringify(pending.args, null, 2)}
            </pre>
          </details>
        </>
      ) : (
        <>
          <div id="chat-confirmation-title" className="text-sm font-medium mb-1 text-text">
            {pending.tool_name}
          </div>
          <pre className="text-[10px] text-muted bg-bg-2 p-2 rounded overflow-x-auto mb-2 whitespace-pre-wrap break-all">
            {JSON.stringify(pending.args, null, 2)}
          </pre>
        </>
      )}
      {edit && <EditCardTable card={edit} guided={Boolean(guidedWording)} />}
      {pending.edit_card !== undefined && !edit && <p role="alert" className="mb-2 text-xs text-warn">
        The old and new values of this change are unavailable. Deny it and ask again.
      </p>}
      {pending.tool_name === 'apply_project_changes' && (review ? (
        <div className="mb-2 text-xs">
          {/* LC-16: the edit card above already lists every row; the bare diff is the fallback. */}
          {!edit && <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <caption className="text-left font-semibold mb-1">Changes to apply</caption>
              <thead><tr>{['Asset', 'Field', 'Old value', 'New value', 'Unit'].map(h => (
                <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>
              ))}</tr></thead>
              <tbody>{review.map((r, i) => <tr key={i}>
                {[`${r.component_class} ${r.name}`, r.attribute, String(r.before), String(r.after),
                  r.unit || 'No unit'].map((v, j) => (
                  <td key={j} className="p-1 border-b border-border break-words">{v}</td>
                ))}
              </tr>)}</tbody>
            </table>
          </div>}
          <p className="mt-1 text-muted">Applies every value shown. Existing time-series overrides remain. Changes are left unsaved.</p>
        </div>
      ) : <p role="alert" className="mb-2 text-xs text-warn">
        The complete change preview is unavailable. Deny this request and ask for a new, smaller preview.
      </p>)}
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
      {mismatchLine && (
        <div className="mb-2 text-[11px] text-warn" data-testid="chat-confirm-mismatch">{mismatchLine}</div>
      )}
      <div className="flex items-center gap-2">
        <button
          className="px-2 py-1 text-xs rounded bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/50 disabled:opacity-40 disabled:cursor-not-allowed"
          onClick={onApprove}
          disabled={!typedSatisfied || !!mismatchLine || reviewUnavailable}
          title={mismatchLine ?? undefined}
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
