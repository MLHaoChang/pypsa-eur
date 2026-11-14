import { useProjectFlow } from './useProjectFlow'

const KIND_WORD = { optimisation: 'Optimisation', study: 'Study', loadflow: 'Load flow' } as const

type KindWord = 'stale' | 'uncompared'

function useRunKindsReading(word: KindWord): string[] {
  const { flow } = useProjectFlow()
  if (!flow || flow.deferred) return []
  const by = flow.sections.find(s => s.section === 'results')?.freshness_by_kind
  if (!by) return []
  const req = flow.run_request
  return (['optimisation', 'study', 'loadflow'] as const)
    .filter(k => req[k] && by[k] === word)
    .map(k => KIND_WORD[k])
}

/** The run kinds the Run requests whose results are older than the inputs
 *  (`results.freshness_by_kind`, W4b), in words: "Optimisation", "Load flow".
 *  A kind the Run does not request, a study-less project's null study and a
 *  flow that could not be read count nothing. */
export function useStaleRunKinds(): string[] {
  return useRunKindsReading('stale')
}

/** The run kinds the Run requests whose results were never checked against
 *  the inputs (`uncompared`, PG-04b): results that came with the project, which
 *  no solve here recorded the inputs of. Never counted as current. */
export function useUncheckedRunKinds(): string[] {
  return useRunKindsReading('uncompared')
}

function kindList(kinds: string[]): string {
  const lower = kinds.map((k, i) => (i === 0 ? k : k.toLowerCase()))
  return lower.length > 1 ? `${lower.slice(0, -1).join(', ')} and ${lower[lower.length - 1]}` : lower[0]
}

/** "Optimisation and load flow results are older than the inputs." */
export function staleKindsSentence(kinds: string[]): string {
  return `${kindList(kinds)} results are older than the inputs.`
}

/** "Optimisation results came with the project and were not checked against its inputs. Run to check them." */
export function uncheckedKindsSentence(kinds: string[]): string {
  return `${kindList(kinds)} results came with the project and were not checked against its inputs. Run to check them.`
}
