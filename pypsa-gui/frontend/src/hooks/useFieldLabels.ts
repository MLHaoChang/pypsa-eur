import { useMemo } from 'react'
import { useHubTemplate } from '../pages/hubDesign/useHubData'
import { ledgerFieldLabels } from '../utils/fieldLabels'

/** Field labels for the open project: the shell's own, plus the labels its
 *  template ledger gives the fields it points at. */
export function useFieldLabels(): Record<string, string> {
  const { template } = useHubTemplate()
  return useMemo(() => ledgerFieldLabels(template), [template])
}
