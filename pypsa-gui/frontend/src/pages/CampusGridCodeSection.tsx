// The project's own grid codes, in the campus electrical panel (plan C10).
//
// A grid code reaches the study in four steps, every one a request:
// 1. upload the code as a PDF (it stays in the project, never in the repository);
// 2. the copilot drafts a profile from it, or the user starts a blank one;
// 3. the user reviews the draft limit by limit, beside the quote, and confirms each;
// 4. the user publishes it, and only then can a study be held to it.
//
// Thin like the panel: everything shown is READ from the backend's
// `/grid-codes` routes, and every refusal renders inline as the backend's
// message (415 not a PDF, 422 names the field, 503 no key, 409 a draft exists
// or limits are unconfirmed).
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FileText, Save, Sparkles, Trash2, Upload } from 'lucide-react'
import {
  campusApi, errorText,
  type GridCodeDocument, type GridCodeDraft, type GridCodeSummary, type GridLimit,
} from '../api/campusElectrical'
import { Btn, Field, PageSection, Tag } from '../components/PageKit'

export const GRID_CODES_KEY = (name: string) => ['campusElectrical', 'gridCodes', name] as const
const DRAFT_KEY = (name: string, id: string) => ['campusElectrical', 'gridCodeDraft', name, id] as const

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
/** The backend's rule for a profile id. */
const PROFILE_ID = /^[a-z0-9_]{1,64}$/
const ID_HINT = 'lowercase letters, digits and _, up to 64'
const NOT_STATED = 'not stated in the document — generic value'
const UNCONFIRMED_CHECK = 'Publish with unconfirmed limits (they will show as extracted on every report)'

const SOURCE_TONE: Record<GridLimit['source'], 'ok' | 'warn' | 'accent'> = {
  code: 'accent', assumed: 'warn', extracted: 'warn',
}

function megabytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** The limit paths of a profile, in the order the backend lists them. */
function limitPaths(profile: GridCodeDraft['profile']): string[] {
  const paths = (profile.voltage_bands ?? []).map((_, i) => `voltage_bands[${i}]`)
  paths.push('q_range_demand', 'rvc_limit_pct')
  if (profile.campus_voltage) paths.push('campus_voltage')
  return paths
}

function limitAt(profile: GridCodeDraft['profile'], path: string): GridLimit | undefined {
  const band = /^voltage_bands\[(\d+)\]$/.exec(path)
  if (band) return profile.voltage_bands?.[Number(band[1])]
  return (profile as unknown as Record<string, GridLimit | undefined>)[path]
}

function limitLabel(path: string): string {
  if (path.startsWith('voltage_bands')) return `Voltage band ${Number(/\[(\d+)\]/.exec(path)![1]) + 1}`
  return {
    q_range_demand: 'Reactive range at the PCC',
    rvc_limit_pct: 'Rapid voltage change',
    campus_voltage: 'Voltage inside the campus',
  }[path] ?? path
}

function limitValue(path: string, lim: GridLimit): string {
  if (path.startsWith('voltage_bands')) return `${lim.kv_min}–${lim.kv_max} kV: ${lim.v_min}–${lim.v_max} pu`
  if (path === 'q_range_demand') return `${lim.value} × P_ref`
  if (path === 'rvc_limit_pct') return `${lim.value} %`
  return `${lim.v_min}–${lim.v_max} pu`
}

export default function CampusGridCodeSection({ name, onProfilesChanged }: {
  name: string
  /** Called after a publish or a delete, so the study's grid-code picker refreshes. */
  onProfilesChanged: () => unknown
}) {
  const qc = useQueryClient()
  const codes = useQuery({ queryKey: GRID_CODES_KEY(name), queryFn: () => campusApi.gridCodes(name), retry: false })
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshList = () => qc.invalidateQueries({ queryKey: GRID_CODES_KEY(name) })
  const showDraft = (d: GridCodeDraft) => {
    qc.setQueryData(DRAFT_KEY(name, d.id), d)
    setOpenId(d.id)
    refreshList()
  }

  const upload = useMutation({
    mutationFn: (file: File) => campusApi.uploadGridCodeDocument(name, file),
    onMutate: () => setError(null),
    onSuccess: () => refreshList(),
    onError: e => setError(errorText(e)),
  })
  const removeDocument = useMutation({
    mutationFn: (id: string) => campusApi.deleteGridCodeDocument(name, id),
    onMutate: () => setError(null),
    onSuccess: () => { refreshList(); onProfilesChanged() },
    onError: e => setError(errorText(e)),
  })
  const removeDraft = useMutation({
    mutationFn: (id: string) => campusApi.deleteGridCodeDraft(name, id),
    onMutate: () => setError(null),
    onSuccess: (_r, id) => {
      if (openId === id) setOpenId(null)
      qc.removeQueries({ queryKey: DRAFT_KEY(name, id) })
      refreshList()
      onProfilesChanged()
    },
    onError: e => setError(errorText(e)),
  })
  const removePublished = useMutation({
    mutationFn: (id: string) => campusApi.deletePublishedGridCode(name, id),
    onMutate: () => setError(null),
    onSuccess: () => { refreshList(); onProfilesChanged() },
    onError: e => setError(errorText(e)),
  })

  const data = codes.data
  const confirmThen = (what: string, run: () => void) => { if (window.confirm(what)) run() }

  return (
    <div data-testid="grid-codes">
      <PageSection title="Grid codes" hint="Upload a grid code, review its limits, publish it for the study">
        <p className="text-[12px] text-muted mb-3">
          The document stays in this project and is never added to the repository. The copilot drafts the
          limits, and a person must confirm each one before it counts as the code.
        </p>
        {codes.isError && <p className="text-[12px] text-danger mb-2">{errorText(codes.error)}</p>}
        {error && <p className="text-[12px] text-danger mb-2">{error}</p>}

        <div className="flex items-end gap-3 flex-wrap mb-3">
          <Field label="Upload a PDF" hint="at most 20 MB and 100 pages">
            <span className="inline-flex items-center gap-2">
              <Upload size={13} className="text-muted" />
              <input
                type="file"
                accept="application/pdf,.pdf"
                aria-label="Grid-code document"
                className="text-[11.5px]"
                disabled={upload.isPending}
                onChange={e => {
                  const file = e.target.files?.[0]
                  e.target.value = ''
                  if (file) upload.mutate(file)
                }}
              />
              {upload.isPending && <span className="text-[11.5px] text-muted">Uploading…</span>}
            </span>
          </Field>
        </div>

        {data && !data.extraction_available && (
          <p className="text-[11.5px] text-warn mb-3">
            Extracting limits needs an Anthropic API key (ANTHROPIC_API_KEY) in the environment settings; a
            session sees a new key only after it restarts. Without one, use New blank grid code and type
            the limits in by hand.
          </p>
        )}

        {data && data.documents.length > 0 && (
          <ul className="mb-3 divide-y divide-border border border-border rounded">
            {data.documents.map(doc => (
              <DocumentRow
                key={doc.id}
                name={name}
                doc={doc}
                available={data.extraction_available}
                onDraft={showDraft}
                onDelete={() => confirmThen(`Delete the document ${doc.filename}? Drafts made from it keep their quotes, but they can no longer be checked against it.`,
                  () => removeDocument.mutate(doc.id))}
              />
            ))}
          </ul>
        )}

        <NewBlank name={name} onDraft={showDraft} />

        {data && data.drafts.length > 0 && (
          <SummaryList
            title="Drafts"
            rows={data.drafts}
            kind="draft"
            onOpen={setOpenId}
            onDelete={s => confirmThen(`Delete the draft ${s.id}? Its limits and its review are lost.`, () => removeDraft.mutate(s.id))}
          />
        )}
        {data && data.published.length > 0 && (
          <SummaryList
            title="Published"
            rows={data.published}
            kind="published"
            onDelete={s => confirmThen(`Delete the published grid code ${s.id}? A study can no longer be held to it.`, () => removePublished.mutate(s.id))}
          />
        )}
      </PageSection>

      {openId && (
        <DraftReview
          key={openId}
          name={name}
          id={openId}
          onDraft={showDraft}
          onPublished={() => { refreshList(); onProfilesChanged() }}
          onDelete={() => confirmThen(`Delete the draft ${openId}? Its limits and its review are lost.`, () => removeDraft.mutate(openId))}
        />
      )}
    </div>
  )
}

function SummaryList({ title, rows, kind, onOpen, onDelete }: {
  title: string
  rows: GridCodeSummary[]
  kind: 'draft' | 'published'
  onOpen?: (id: string) => void
  onDelete: (s: GridCodeSummary) => void
}) {
  return (
    <div className="mt-3">
      <div className="text-[10.5px] font-medium text-muted mb-1">{title}</div>
      <ul className="divide-y divide-border border border-border rounded">
        {rows.map(s => (
          <li key={s.id} data-testid={`${kind}-${s.id}`} className="flex items-center gap-2 px-3 py-1.5 text-[12px]">
            <span className="font-mono">{s.id}</span>
            <span className="text-muted truncate flex-1">{s.title}</span>
            {s.error && <Tag tone="err">{s.error}</Tag>}
            {s.unconfirmed.length > 0 && <Tag tone="warn">{s.unconfirmed.length} unconfirmed</Tag>}
            {onOpen && <Btn onClick={() => onOpen(s.id)} aria-label={`Review ${kind} ${s.id}`}>Review</Btn>}
            <Btn onClick={() => onDelete(s)} aria-label={`Delete ${kind} ${s.id}`}><Trash2 size={13} /></Btn>
          </li>
        ))}
      </ul>
    </div>
  )
}

function DocumentRow({ name, doc, available, onDraft, onDelete }: {
  name: string
  doc: GridCodeDocument
  available: boolean
  onDraft: (d: GridCodeDraft) => void
  onDelete: () => void
}) {
  const [profileId, setProfileId] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)
  const [exists, setExists] = useState(false)
  const extract = useMutation({
    mutationFn: (overwrite: boolean) => campusApi.extractGridCode(name, doc.id, {
      profile_id: profileId.trim() === '' ? undefined : profileId.trim(), overwrite,
    }),
    onMutate: () => { setRefusal(null); setExists(false) },
    onSuccess: onDraft,
    onError: e => {
      setRefusal(errorText(e))
      setExists((e as { response?: { status?: number } } | null)?.response?.status === 409)
    },
  })
  const idOk = profileId.trim() === '' || PROFILE_ID.test(profileId.trim())

  return (
    <li data-testid={`document-${doc.id}`} className="px-3 py-2 text-[12px]">
      <div className="flex items-center gap-2 flex-wrap">
        <FileText size={13} className="text-muted shrink-0" />
        <span className="font-medium">{doc.filename}</span>
        <span className="text-muted">{doc.pages} pages · {megabytes(doc.size)}</span>
        <span className="flex-1" />
        <Field label="Profile id" row>
          <input
            aria-label="Profile id"
            className={`${INPUT} w-[170px] font-mono`}
            value={profileId}
            placeholder={`gc_${doc.id.slice(0, 12)}`}
            spellCheck={false}
            onChange={e => setProfileId(e.target.value)}
          />
        </Field>
        <Btn
          variant="primary"
          disabled={!available || extract.isPending || !idOk}
          title={available ? undefined : 'Needs an Anthropic API key in the environment settings'}
          onClick={() => extract.mutate(false)}
        >
          <Sparkles size={13} /> {extract.isPending ? 'Extracting…' : 'Extract limits'}
        </Btn>
        <Btn onClick={onDelete} aria-label={`Delete document ${doc.filename}`}><Trash2 size={13} /></Btn>
      </div>
      {!idOk && <p className="text-[11px] text-danger mt-1">A profile id is {ID_HINT}.</p>}
      {extract.isPending && (
        <p className="text-[11px] text-muted mt-1">The copilot is reading the document; this can take a minute or two.</p>
      )}
      {refusal && (
        <p className="text-[12px] text-danger mt-1">
          {refusal}{' '}
          {exists && <Btn onClick={() => extract.mutate(true)}>Replace the existing draft</Btn>}
        </p>
      )}
    </li>
  )
}

function NewBlank({ name, onDraft }: { name: string; onDraft: (d: GridCodeDraft) => void }) {
  const [profileId, setProfileId] = useState('')
  const [title, setTitle] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)
  const [exists, setExists] = useState(false)
  const create = useMutation({
    mutationFn: (overwrite: boolean) => campusApi.newGridCodeDraft(name, {
      profile_id: profileId.trim(), title: title.trim() === '' ? undefined : title.trim(), overwrite,
    }),
    onMutate: () => { setRefusal(null); setExists(false) },
    onSuccess: onDraft,
    onError: e => {
      setRefusal(errorText(e))
      setExists((e as { response?: { status?: number } } | null)?.response?.status === 409)
    },
  })
  const idOk = PROFILE_ID.test(profileId.trim())
  return (
    <div>
      <div className="flex items-end gap-3 flex-wrap">
        <Field label="Profile id" hint={ID_HINT}>
          <input aria-label="New grid code id" className={`${INPUT} w-[170px] font-mono`} value={profileId}
                 spellCheck={false} onChange={e => setProfileId(e.target.value)} />
        </Field>
        <Field label="Title">
          <input aria-label="New grid code title" className={`${INPUT} w-[280px]`} value={title}
                 onChange={e => setTitle(e.target.value)} />
        </Field>
        <Btn disabled={!idOk || create.isPending} onClick={() => create.mutate(false)}>
          New blank grid code
        </Btn>
      </div>
      <p className="text-[11px] text-muted mt-1">
        The manual path: a draft with generic limits, which you edit by hand. It needs no API key.
      </p>
      {refusal && (
        <p className="text-[12px] text-danger mt-1">
          {refusal}{' '}
          {exists && <Btn onClick={() => create.mutate(true)}>Replace the existing draft</Btn>}
        </p>
      )}
    </div>
  )
}

function DraftReview({ name, id, onDraft, onPublished, onDelete }: {
  name: string
  id: string
  onDraft: (d: GridCodeDraft) => void
  onPublished: () => void
  onDelete: () => void
}) {
  const query = useQuery({ queryKey: DRAFT_KEY(name, id), queryFn: () => campusApi.getGridCodeDraft(name, id), retry: false })
  const draft = query.data
  const [text, setText] = useState(draft?.yaml ?? '')
  const [allow, setAllow] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [published, setPublished] = useState<string | null>(null)
  useEffect(() => { if (draft) setText(draft.yaml) }, [draft?.yaml]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () => campusApi.saveGridCodeDraft(name, id, text),
    onMutate: () => { setRefusal(null); setPublished(null) },
    onSuccess: onDraft,
    onError: e => setRefusal(errorText(e)),
  })
  const confirm = useMutation({
    mutationFn: (limit: string) => campusApi.confirmGridCodeLimit(name, id, limit),
    onMutate: () => { setRefusal(null); setPublished(null) },
    onSuccess: onDraft,
    onError: e => setRefusal(errorText(e)),
  })
  const publish = useMutation({
    mutationFn: () => campusApi.publishGridCode(name, id, allow),
    onMutate: () => { setRefusal(null); setPublished(null) },
    onSuccess: r => {
      setPublished(r.unconfirmed.length > 0
        ? `Published as ${r.id}, with ${r.unconfirmed.length} unconfirmed limit${r.unconfirmed.length === 1 ? '' : 's'} that every report row will show as extracted.`
        : `Published as ${r.id}. It is in the study's grid-code list.`)
      onPublished()
    },
    onError: e => setRefusal(errorText(e)),
  })

  if (!draft) {
    return (
      <PageSection title={`Draft ${id}`}>
        <p className="text-[12px] text-muted">{query.isError ? errorText(query.error) : 'Loading…'}</p>
      </PageSection>
    )
  }

  const dirty = text !== draft.yaml
  const open = draft.unconfirmed.length
  const review = draft.review
  const filled = new Set(review?.filled_from_template ?? [])

  return (
    <PageSection
      title={`Draft ${draft.id}`}
      hint={draft.profile.title}
      right={<Btn onClick={onDelete}><Trash2 size={13} /> Delete draft</Btn>}
    >
      {refusal && <p className="text-[12px] text-danger mb-2">{refusal}</p>}
      {published && <p className="text-[12px] text-success mb-2">{published}</p>}
      <p className="text-[11.5px] text-muted mb-2">
        {draft.document
          ? <>From {draft.document.filename}. </>
          : <>No document is attached to this draft. </>}
        {open > 0
          ? `${open} limit${open === 1 ? '' : 's'} still extracted: confirm each against its quote.`
          : 'Every limit is confirmed or assumed.'}
      </p>

      <table className="w-full text-[12px] mb-3">
        <tbody>
          {limitPaths(draft.profile).map(path => {
            const lim = limitAt(draft.profile, path)
            if (!lim) return null
            const check = review?.limits[path]
            // `voltage_bands` (the model returned none) marks every band; `voltage_bands[i]`
            // (a gap the extraction filled) marks that band only.
            const notStated = filled.has(path) || filled.has(path.split('[')[0])
            return (
              <tr key={path} data-testid={`limit-${path}`} className="border-t border-border first:border-0 align-top">
                <td className="py-2 pr-3 whitespace-nowrap">
                  <div className="font-medium">{limitLabel(path)}</div>
                  <div className="font-mono text-[10.5px] text-muted">{path}</div>
                </td>
                <td className="py-2 pr-3 tabular-nums whitespace-nowrap">{limitValue(path, lim)}</td>
                <td className="py-2 pr-3">
                  <div className="flex items-start gap-1.5">
                    <Tag tone={SOURCE_TONE[lim.source]}>{lim.source}</Tag>
                    <span className="text-muted">{lim.clause}</span>
                  </div>
                  {notStated && <div className="text-[11px] text-warn mt-1">{NOT_STATED}</div>}
                  {lim.quote != null && (
                    <blockquote className="mt-1 pl-2 border-l-2 border-border text-[11.5px]">
                      {lim.quote}
                      {lim.page != null && <span className="text-muted"> — page {lim.page}</span>}
                    </blockquote>
                  )}
                  {check?.quote_found === false && typeof check.found_on_page === 'number' && (
                    <div className="text-[11px] text-danger mt-1">
                      Quote found on page {check.found_on_page}, not on page {lim.page} (as stated). Check it against the document before confirming.
                    </div>
                  )}
                  {check?.quote_found === false && typeof check.found_on_page !== 'number' && (
                    <div className="text-[11px] text-danger mt-1">Quote not found in the document. Check it against the document before confirming.</div>
                  )}
                  {check?.quote_found === null && (
                    <div className="text-[11px] text-muted mt-1">The document is no longer in the project, so the quote was not checked.</div>
                  )}
                </td>
                <td className="py-2 text-right">
                  {lim.source === 'extracted' && (
                    <Btn
                      onClick={() => confirm.mutate(path)}
                      disabled={confirm.isPending || dirty}
                      aria-label={`Confirm ${path}`}
                    >
                      <Check size={13} /> Confirm
                    </Btn>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <textarea
        aria-label="Grid-code draft"
        className={`${INPUT} w-full font-mono text-[11.5px] h-[220px]`}
        spellCheck={false}
        value={text}
        onChange={e => setText(e.target.value)}
      />
      <div className="flex items-center justify-between gap-3 mt-2 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          {open > 0 && (
            <label className="flex items-center gap-2 text-[11.5px]">
              <input type="checkbox" aria-label={UNCONFIRMED_CHECK} checked={allow} onChange={e => setAllow(e.target.checked)} />
              {UNCONFIRMED_CHECK}
            </label>
          )}
          {dirty && <span className="text-[11px] text-warn">Save your edits before confirming or publishing.</span>}
        </div>
        <div className="flex items-center gap-2">
          {dirty && <Btn onClick={() => setText(draft.yaml)}>Discard edits</Btn>}
          <Btn onClick={() => save.mutate()} disabled={save.isPending || !dirty || !text.trim()}>
            <Save size={13} /> Save draft
          </Btn>
          <Btn
            variant="primary"
            onClick={() => publish.mutate()}
            disabled={publish.isPending || dirty || (open > 0 && !allow)}
          >
            Publish
          </Btn>
        </div>
      </div>
    </PageSection>
  )
}
