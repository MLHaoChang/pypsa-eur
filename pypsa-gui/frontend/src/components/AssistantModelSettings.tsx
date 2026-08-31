/**
 * Task 15 — the super-admin surface for LLM connection profiles: which one
 * answers in chat, its key, whether it currently connects, add/delete.
 *
 * HOSTING. Rendered from pages/LocalSettings.tsx, ABOVE that pane's
 * `state == null` early return, because this section's own availability is
 * independent of the desktop-only local-settings routes — see
 * useLLMSettings.ts. Section anchor id `assistant-model` is how
 * requestSettingsSection('assistant-model') (uiStore) finds this element to
 * scroll it into view after the settings SlidePanel remounts.
 *
 * SECURITY. `LLMProfileOut` carries `key_present`/`key_hint` (last four
 * characters) and NEVER a key value (api/llmSettings.ts's own header
 * explains why). This component must not — and does not — ever seed an
 * input with a stored value: `keyDrafts` starts and is reset to `''`, never
 * to `profile.key_hint`. Connection-test copy is keyed off the typed
 * `TestVerdict`, never off a server-supplied message string, so it cannot
 * leak a base_url or upstream exception text either.
 *
 * FOUR-STATE DISCIPLINE (fix round 1, ADR-0001 in a new place — matches how
 * Task 13 handles the chat profile dropdown): loading / error / "not for
 * you" / ready are four DISTINCT renders, not three:
 *   - loading  → nothing yet (`isLoading`).
 *   - error    → a visible, labelled outage state with a retry affordance
 *                (`isError`) — NEVER the same as "hidden" or "no profiles".
 *   - not for you → nothing, permanently for this session (`data === null`,
 *                a 403/404 `fetchLLMSettingsOrNull` mapped for us).
 *   - ready    → the profile list (`data` is the payload).
 * `useLocalSettings` (hooks/useLocalSettings.ts) still collapses error into
 * its null/"not available" state — that is a DELIBERATE, pre-existing
 * choice this task does not touch, not an inconsistency to fix here; see
 * that hook's own header.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

import {
  deleteLLMProfile,
  deleteLLMProfileKey,
  postLLMActive,
  postLLMTest,
  putLLMProfile,
  putLLMProfileKey,
  type LLMProfileIn,
  type LLMProfileOut,
  type PresetOut,
  type TestVerdict,
} from '../api/llmSettings'
import { useInvalidateLLMSettings, useLLMSettings } from '../hooks/useLLMSettings'
import { CHAT_PROFILES_QUERY_KEY } from '../hooks/useChatProfiles'
import { useUIStore } from '../store/uiStore'
import { confirmToast } from '../utils/toasts'
import { ConfirmDialog } from './ConfirmDialog'

/**
 * The two ids `services/llm_config.py` synthesizes in code and never lets a
 * file entry override — see `_BUILTIN_IDS` there. Exported so ApiKeySetup
 * can ask the same question ("is the built-in Anthropic profile active?")
 * without duplicating the literal ids.
 */
export const BUILTIN_ANTHROPIC_PROFILE_IDS = new Set(['anthropic-sonnet', 'anthropic-opus'])

function isLocalHost(baseUrl: string | null): boolean {
  if (!baseUrl) return false
  try {
    const { hostname } = new URL(baseUrl)
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '0.0.0.0'
      || hostname.endsWith('.local')
  } catch {
    return false
  }
}

/**
 * Fixed copy per verdict — deliberately not derived from anything the server
 * sent, per the file header's security note. `unreachable` alone branches on
 * whether the profile's base_url looks local, per the spec ("say the
 * endpoint may not be running"); it names no host, port or path.
 */
function verdictCopy(
  verdict: TestVerdict,
  ctx: { latencyMs: number | null; isLocal: boolean },
): string {
  switch (verdict) {
    case 'ok':
      return ctx.latencyMs != null ? `Connected — ${ctx.latencyMs} ms.` : 'Connected.'
    case 'unreachable':
      return ctx.isLocal
        ? 'Could not reach the endpoint — it may not be running. Start it and test again.'
        : 'Could not reach the endpoint. Check that it is online and reachable from this machine.'
    case 'unauthorized':
      return 'The endpoint rejected the key. Check that it is correct and has not expired.'
    case 'model_not_found':
      return "The endpoint doesn't recognize this model id. Check the spelling, or pick a suggested model."
    case 'invalid_request':
      return 'The request was rejected as malformed. Check the model id and profile settings.'
  }
}

/** A profile's key status line — never the value, per the file header. */
function keyStatusText(profile: LLMProfileOut): string {
  if (profile.auth === 'none') return 'No key needed'
  if (profile.key_hint) return `Key set — ending ${profile.key_hint}`
  if (BUILTIN_ANTHROPIC_PROFILE_IDS.has(profile.id)) {
    return 'Uses ANTHROPIC_API_KEY from the environment'
  }
  return 'No key set'
}

function slugify(label: string): string {
  const slug = label.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return (slug || 'profile').slice(0, 48)
}

function uniqueId(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base
  for (let n = 2; ; n++) {
    const candidate = `${base.slice(0, 44)}-${n}`
    if (!taken.has(candidate)) return candidate
  }
}

type TestState = { verdict: TestVerdict; latencyMs: number | null } | 'pending' | 'error'

interface ProfileRowProps {
  profile: LLMProfileOut
  active: boolean
  onActivate: () => void
  keyDraft: string
  onKeyDraftChange: (v: string) => void
  onSaveKey: () => void
  onClearKey: () => void
  savingKey: boolean
  testState: TestState | undefined
  onTest: () => void
  onDelete: () => void
}

function ProfileRow({
  profile, active, onActivate,
  keyDraft, onKeyDraftChange, onSaveKey, onClearKey, savingKey,
  testState, onTest, onDelete,
}: ProfileRowProps) {
  const testing = testState === 'pending'
  return (
    <li
      className="rounded border border-border p-3 space-y-2"
      data-testid={`assistant-model-row-${profile.id}`}
    >
      <div className="flex items-start gap-2">
        <input
          type="radio"
          name="assistant-active-profile"
          checked={active}
          onChange={onActivate}
          aria-label={`Use ${profile.label} as the active model`}
          data-testid={`assistant-model-radio-${profile.id}`}
          className="mt-1"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{profile.label}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel border border-border text-muted">
              {profile.wire}
            </span>
            {active && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                Active
              </span>
            )}
          </div>
          <p className="text-xs text-muted">{profile.model}</p>
        </div>
        <button
          type="button"
          className="text-[11px] underline text-muted hover:text-danger"
          data-testid={`assistant-model-delete-${profile.id}`}
          onClick={onDelete}
        >
          Delete
        </button>
      </div>

      <p className="text-xs text-muted">{keyStatusText(profile)}</p>

      {profile.auth === 'bearer' && (
        <div className="flex items-center gap-2">
          <input
            type="password"
            className="flex-1 rounded border border-border bg-bg px-2 py-1 text-xs text-text focus:outline-none focus:border-accent"
            placeholder="Paste a new key…"
            value={keyDraft}
            onChange={e => onKeyDraftChange(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            data-testid={`assistant-model-key-input-${profile.id}`}
          />
          <button
            type="button"
            className="rounded border border-border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!keyDraft.trim() || savingKey}
            onClick={onSaveKey}
            data-testid={`assistant-model-key-save-${profile.id}`}
          >
            Save
          </button>
          {profile.key_present && (
            <button
              type="button"
              className="rounded border border-border px-2 py-1 text-xs disabled:opacity-50"
              onClick={onClearKey}
              data-testid={`assistant-model-key-clear-${profile.id}`}
            >
              Clear
            </button>
          )}
        </div>
      )}

      <div>
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-xs disabled:opacity-50"
          disabled={testing}
          onClick={onTest}
          data-testid={`assistant-model-test-${profile.id}`}
        >
          {testing ? 'Testing…' : 'Test connection'}
        </button>
        {testState && testState !== 'pending' && (
          <p
            className={`mt-1 text-xs ${testState === 'error' || testState.verdict !== 'ok' ? 'text-danger' : 'text-success'}`}
            data-testid={`assistant-model-test-result-${profile.id}`}
          >
            {testState === 'error'
              ? 'Could not run the test — try again.'
              : verdictCopy(testState.verdict, {
                latencyMs: testState.latencyMs,
                isLocal: isLocalHost(profile.base_url),
              })}
          </p>
        )}
      </div>
    </li>
  )
}

interface AddProfileFormProps {
  presets: PresetOut[]
  takenIds: ReadonlySet<string>
  pending: boolean
  onCancel: () => void
  onSubmit: (id: string, body: LLMProfileIn) => void
}

function AddProfileForm({ presets, takenIds, pending, onCancel, onSubmit }: AddProfileFormProps) {
  const [presetId, setPresetId] = useState<string>('custom')
  const [label, setLabel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [wire, setWire] = useState<'anthropic' | 'openai'>('openai')
  const [auth, setAuth] = useState<'bearer' | 'none'>('bearer')
  const [tools, setTools] = useState(true)
  const [vision, setVision] = useState(true)

  const preset = presets.find(p => p.id === presetId) ?? null
  const suggestedModels = preset?.suggested_models ?? []

  function applyPreset(id: string) {
    setPresetId(id)
    const p = presets.find(x => x.id === id)
    if (!p) return
    setLabel(p.label)
    setBaseUrl(p.base_url ?? '')
    setWire(p.wire)
    setAuth(p.auth)
    setTools(p.tools)
    setVision(p.vision)
  }

  function submit() {
    const trimmedLabel = label.trim()
    const trimmedModel = model.trim()
    if (!trimmedLabel || !trimmedModel) return
    const id = uniqueId(slugify(trimmedLabel), takenIds)
    const body: LLMProfileIn = {
      label: trimmedLabel,
      preset: preset ? preset.id : 'custom',
      wire,
      base_url: baseUrl.trim() || null,
      model: trimmedModel,
      tools,
      vision,
      auth,
    }
    onSubmit(id, body)
  }

  return (
    <div className="rounded border border-border p-3 space-y-2" data-testid="assistant-model-add-form">
      <div>
        <label className="text-xs text-muted block mb-1" htmlFor="assistant-model-add-preset">
          Start from a preset
        </label>
        <select
          id="assistant-model-add-preset"
          className="w-full rounded border border-border bg-bg px-2 py-1 text-xs"
          value={presetId}
          onChange={e => applyPreset(e.target.value)}
          data-testid="assistant-model-add-preset"
        >
          <option value="custom">Custom endpoint</option>
          {presets.map(p => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
      </div>

      <input
        className="w-full rounded border border-border bg-bg px-2 py-1 text-xs"
        placeholder="Label"
        value={label}
        onChange={e => setLabel(e.target.value)}
        data-testid="assistant-model-add-label"
      />
      <input
        className="w-full rounded border border-border bg-bg px-2 py-1 text-xs"
        placeholder="Base URL (leave blank for the wire's default endpoint)"
        value={baseUrl}
        onChange={e => setBaseUrl(e.target.value)}
        data-testid="assistant-model-add-base-url"
      />
      <input
        className="w-full rounded border border-border bg-bg px-2 py-1 text-xs"
        placeholder="Model id"
        list="assistant-model-add-model-suggestions"
        value={model}
        onChange={e => setModel(e.target.value)}
        data-testid="assistant-model-add-model"
      />
      <datalist id="assistant-model-add-model-suggestions">
        {suggestedModels.map(m => <option key={m} value={m} />)}
      </datalist>

      <div className="flex items-center gap-4 text-xs">
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={tools}
            onChange={e => setTools(e.target.checked)}
            data-testid="assistant-model-add-tools"
          />
          Tools
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={vision}
            onChange={e => setVision(e.target.checked)}
            data-testid="assistant-model-add-vision"
          />
          Vision
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={auth === 'bearer'}
            onChange={e => setAuth(e.target.checked ? 'bearer' : 'none')}
            data-testid="assistant-model-add-auth"
          />
          Needs a key
        </label>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-border px-2 py-1 text-xs disabled:opacity-50"
          disabled={pending || !label.trim() || !model.trim()}
          onClick={submit}
          data-testid="assistant-model-add-submit"
        >
          {pending ? 'Saving…' : 'Save model'}
        </button>
        <button
          type="button"
          className="text-xs text-muted underline"
          onClick={onCancel}
          data-testid="assistant-model-add-cancel"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function AssistantModelSettings() {
  const { data, isLoading, isError, refetch } = useLLMSettings()
  const invalidateLLM = useInvalidateLLMSettings()
  const qc = useQueryClient()
  const sectionRef = useRef<HTMLDivElement>(null)
  const settingsSectionRequest = useUIStore(s => s.settingsSectionRequest)
  const clearSettingsSectionRequest = useUIStore(s => s.clearSettingsSectionRequest)

  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [testResults, setTestResults] = useState<Record<string, TestState>>({})
  const [deleteTarget, setDeleteTarget] = useState<LLMProfileOut | null>(null)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    if (settingsSectionRequest !== 'assistant-model') return
    // The section renders nothing while `isLoading` or `data == null` (see
    // the early returns below), so on the FIRST commit `sectionRef.current`
    // is null — nothing to scroll to yet. Without this guard the request
    // would still get cleared here (a bare `?.` on the scroll call swallows
    // the miss), and by the time the real content mounts the request is
    // already gone: the deep-link would silently fail to scroll. Waiting for
    // a real node, and re-running once `isLoading` flips, is what makes this
    // survive the loading gap.
    if (!sectionRef.current) return
    sectionRef.current.scrollIntoView({ block: 'start' })
    clearSettingsSectionRequest()
  }, [settingsSectionRequest, clearSettingsSectionRequest, isLoading, data, isError])

  function refreshAll() {
    void invalidateLLM()
    void qc.invalidateQueries({ queryKey: CHAT_PROFILES_QUERY_KEY })
    void qc.invalidateQueries({ queryKey: ['chat', 'health'] })
  }

  const activateMutation = useMutation({
    mutationFn: (id: string) => postLLMActive(id),
    onSuccess: refreshAll,
    onError: () => toast.error('Could not switch the active model.'),
  })

  const saveKeyMutation = useMutation({
    mutationFn: (vars: { id: string; value: string }) => putLLMProfileKey(vars.id, vars.value),
    onSuccess: (_r, vars) => {
      setKeyDrafts(d => ({ ...d, [vars.id]: '' }))
      refreshAll()
      toast.success('Key saved.')
    },
    onError: () => toast.error('Could not save this key.'),
  })

  const clearKeyMutation = useMutation({
    mutationFn: (id: string) => deleteLLMProfileKey(id),
    onSuccess: (_r, id) => {
      setKeyDrafts(d => ({ ...d, [id]: '' }))
      refreshAll()
      toast.success('Key removed.')
    },
    onError: () => toast.error('Could not remove this key.'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteLLMProfile(id),
    onSuccess: () => {
      setDeleteTarget(null)
      refreshAll()
      toast.success('Profile deleted.')
    },
    onError: () => toast.error('Could not delete this profile.'),
  })

  const addMutation = useMutation({
    mutationFn: (vars: { id: string; body: LLMProfileIn }) => putLLMProfile(vars.id, vars.body),
    onSuccess: () => {
      setAdding(false)
      refreshAll()
      toast.success('Model added.')
    },
    onError: () => toast.error('Could not save this profile.'),
  })

  async function runTest(id: string) {
    setTestResults(r => ({ ...r, [id]: 'pending' }))
    try {
      const result = await postLLMTest(id)
      setTestResults(r => ({ ...r, [id]: { verdict: result.verdict, latencyMs: result.latency_ms } }))
    } catch {
      setTestResults(r => ({ ...r, [id]: 'error' }))
    }
  }

  function requestClearKey(profile: LLMProfileOut) {
    confirmToast(
      `Remove the stored key for "${profile.label}"? This model will stop working until a new key is set.`,
      () => clearKeyMutation.mutate(profile.id),
      { confirmLabel: 'Remove', danger: true },
    )
  }

  if (isLoading) return null

  // Fix round 1 — ADR-0001 in a new place: an outage must not render
  // identically to "not for you". `fetchLLMSettingsOrNull` (api/llmSettings.ts)
  // maps ONLY 403/404 to `data === null`; anything else (a 500, a network
  // drop) rethrows and settles this query into `isError`, with `data` left
  // `undefined`. Checking `data == null` alone (loose equality also matches
  // `undefined`) would have collapsed those two very different situations
  // into the same silent nothing — an admin-facing diagnostic surface
  // vanishing during a real outage is worse than showing nothing to a member
  // it was never for. This branch MUST be checked before the `data == null`
  // one below, and must render something visibly distinct from both "hidden"
  // and the ready state's profile list — never the same shape as "no
  // profiles configured".
  if (isError) {
    return (
      <div
        ref={sectionRef}
        id="assistant-model"
        data-testid="assistant-model-settings-error"
        className="rounded border border-danger/40 bg-danger/5 p-3 space-y-2 text-xs text-danger"
      >
        <p>Could not load model settings.</p>
        <button
          type="button"
          className="underline"
          onClick={() => void refetch()}
          data-testid="assistant-model-settings-retry"
        >
          Retry
        </button>
      </div>
    )
  }

  // `null` (not `undefined` — see above) means "not for you" — a 403
  // (ordinary member) or 404 (route not mounted). See fetchLLMSettingsOrNull
  // in api/llmSettings.ts.
  if (data == null) return null

  const { profiles, active_profile_id, presets } = data
  const takenIds = new Set(profiles.map(p => p.id))

  return (
    <div ref={sectionRef} id="assistant-model" data-testid="assistant-model-settings" className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Assistant model</h3>
        <p className="text-xs text-muted">
          Choose which model answers in the chat assistant, and manage its connection.
        </p>
      </div>

      <ul className="space-y-2">
        {profiles.map(p => (
          <ProfileRow
            key={p.id}
            profile={p}
            active={p.id === active_profile_id}
            onActivate={() => activateMutation.mutate(p.id)}
            keyDraft={keyDrafts[p.id] ?? ''}
            onKeyDraftChange={v => setKeyDrafts(d => ({ ...d, [p.id]: v }))}
            onSaveKey={() => saveKeyMutation.mutate({ id: p.id, value: keyDrafts[p.id] ?? '' })}
            onClearKey={() => requestClearKey(p)}
            savingKey={saveKeyMutation.isPending && saveKeyMutation.variables?.id === p.id}
            testState={testResults[p.id]}
            onTest={() => runTest(p.id)}
            onDelete={() => setDeleteTarget(p)}
          />
        ))}
      </ul>

      {!adding ? (
        <button
          type="button"
          className="text-xs underline text-muted hover:text-text"
          data-testid="assistant-model-add-open"
          onClick={() => setAdding(true)}
        >
          Add model
        </button>
      ) : (
        <AddProfileForm
          presets={presets}
          takenIds={takenIds}
          pending={addMutation.isPending}
          onCancel={() => setAdding(false)}
          onSubmit={(id, body) => addMutation.mutate({ id, body })}
        />
      )}

      <ConfirmDialog
        open={deleteTarget != null}
        title="Delete model"
        message={deleteTarget ? `Delete the "${deleteTarget.label}" profile? This cannot be undone.` : ''}
        confirmLabel="Delete"
        danger
        pending={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
