// The asset library, in the campus electrical panel (plan C9).
//
// The investment step buys transformers, cables, compensation and switchgear
// from a library. The shipped default is used until the user saves a copy for
// the project; this section edits that copy, as YAML text. Thin like the rest
// of the panel: the text and the badge are READ from `GET /library`, Save and
// Reset are one request each, and a refusal renders inline as the backend's
// message (422 names the entry and the field, 413 over 1 MB).
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, Save } from 'lucide-react'
import { campusApi, errorText, type AssetLibrary } from '../api/campusElectrical'
import { Btn, PageSection, Tag } from '../components/PageKit'

export const LIBRARY_KEY = (name: string) => ['campusElectrical', 'library', name] as const

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

export default function CampusLibrarySection({ name, onChanged }: {
  name: string
  /** Called after a save or a reset: the study's results may now be stale. */
  onChanged: () => unknown
}) {
  const qc = useQueryClient()
  const library = useQuery({ queryKey: LIBRARY_KEY(name), queryFn: () => campusApi.library(name), retry: false })
  const data = library.data
  const [text, setText] = useState('')
  const [refusal, setRefusal] = useState<string | null>(null)
  useEffect(() => { if (data) setText(data.yaml) }, [data?.yaml])   // eslint-disable-line react-hooks/exhaustive-deps

  const kept = (d: AssetLibrary) => { qc.setQueryData(LIBRARY_KEY(name), d); onChanged() }
  const save = useMutation({
    mutationFn: () => campusApi.saveLibrary(name, text),
    onMutate: () => setRefusal(null),
    onSuccess: kept,
    onError: e => setRefusal(errorText(e)),
  })
  const reset = useMutation({
    mutationFn: () => campusApi.resetLibrary(name),
    onMutate: () => setRefusal(null),
    onSuccess: kept,
    onError: e => setRefusal(errorText(e)),
  })
  const dirty = data != null && text !== data.yaml

  return (
    <div data-testid="asset-library">
      <PageSection
        title="Library"
        hint="What the investment step may buy, and what it costs"
        right={data && (
          <span data-testid="library-badge">
            <Tag tone={data.is_default ? 'neutral' : 'accent'}>{data.is_default ? 'default library' : 'project library'}</Tag>
          </span>
        )}
      >
        <p className="text-[12px] text-muted mb-2">
          Every cost in the shipped library is an assumed placeholder, an order of magnitude and not a price:
          replace it with quotes. Saving keeps a copy for this project, validated before it is kept.
        </p>
        {library.isError && <p className="text-[12px] text-danger mb-2">{errorText(library.error)}</p>}
        {refusal && <p className="text-[12px] text-danger mb-2">{refusal}</p>}
        <textarea
          aria-label="Asset library"
          className={`${INPUT} w-full font-mono text-[11.5px] h-[260px]`}
          spellCheck={false}
          value={text}
          onChange={e => setText(e.target.value)}
        />
        <div className="flex justify-end gap-2 mt-2">
          <Btn
            onClick={() => {
              if (window.confirm('Reset the library? This deletes the project copy and replaces your library with the shipped default.')) {
                reset.mutate()
              }
            }}
            disabled={reset.isPending || !data || data.is_default}
          >
            <RotateCcw size={13} /> Reset to default
          </Btn>
          <Btn onClick={() => save.mutate()} disabled={save.isPending || !dirty || !text.trim()}>
            <Save size={13} /> Save library
          </Btn>
        </div>
      </PageSection>
    </div>
  )
}
