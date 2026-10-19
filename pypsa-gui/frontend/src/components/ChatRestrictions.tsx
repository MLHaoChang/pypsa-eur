import { useId, useState } from 'react'
import { saveChatRestriction, type ChatAccessSnapshot, type ChatRestriction,
  type ChatRestrictionMode } from '../api/chatAccess'

export default function ChatRestrictions({ current, onSaved }: {
  current: ChatAccessSnapshot | undefined; onSaved: () => void
}) {
  const id = useId()
  const [draft, setDraft] = useState<ChatRestriction | null>(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ kind: 'saved'; revision: string } | { kind: 'error' } | null>(null)
  const rule = current?.restrictions
  const project = current?.scope.project_id
  const available = !!project && !!rule && typeof rule.revision === 'string'
    && ['standard', 'confirm_changes', 'read_only'].includes(rule.mode)
  const selected = draft?.revision === rule?.revision ? draft?.mode : rule?.mode
  const save = async () => {
    if (!available || !project || !rule || !selected || saving || selected === rule.mode) return
    setSaving(true)
    setResult(null)
    try {
      const response = await saveChatRestriction(project, rule.revision, selected)
      if (response.project_id !== project || response.mode !== selected
        || typeof response.revision !== 'string' || !response.revision.length
        || response.revision === rule.revision) throw new Error('Mismatched save')
      setResult({ kind: 'saved', revision: response.revision })
      onSaved()
    } catch {
      setResult({ kind: 'error' })
    } finally {
      setSaving(false)
    }
  }
  return <fieldset className="border border-border rounded p-2 space-y-2">
    <legend className="px-1 font-semibold">Personal project restrictions</legend>
    <p>These rules apply to your assistant tools in this saved project. They can add restrictions;
      standard rules keep mandatory confirmations.</p>
    <label htmlFor={id}>Your saved rule</label>
    <select id={id} className="block w-full bg-surface border border-border rounded p-1"
      value={selected ?? 'standard'} disabled={!available || saving}
      onChange={event => {
        if (!rule) return
        setDraft({ revision: rule.revision, mode: event.target.value as ChatRestrictionMode })
        setResult(null)
      }}>
      <option value="standard">Standard rules</option>
      <option value="confirm_changes">Ask before changes or execution</option>
      <option value="read_only">Read-only tools</option>
    </select>
    {available && selected !== rule?.mode && !saving && result?.kind !== 'saved'
      && <p>Unsaved choice. Save to apply it.</p>}
    <button type="button" className="underline disabled:opacity-50"
      disabled={!available || saving || selected === rule?.mode} onClick={() => { void save() }}>
      {saving ? 'Saving restriction…' : 'Save restriction'}
    </button>
    {result?.kind === 'saved' && result.revision === rule?.revision && <p role="status">Restriction saved.</p>}
    {result?.kind === 'error' && <p role="alert">Save could not be confirmed. Refresh access and choose again.</p>}
    {!available && <p>Current saved-project rules are unavailable. Open a saved project and refresh access.</p>}
    <p>Saving does not cancel work that has started. Changed rules invalidate waiting actions.
      Tool restrictions do not change configured usage limits.</p>
  </fieldset>
}
