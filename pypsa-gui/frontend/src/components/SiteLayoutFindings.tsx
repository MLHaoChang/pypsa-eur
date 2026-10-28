// The 3D view's layout readout (visual-layers plan 3 S2): what the
// placement check found, as text under the legend — so no finding is
// outline-only. Each line names the object and selects it on click (the
// same deep link the Issues panel offers). Collapsed when there is nothing
// to say; open when there is. DOM only, no three.
import { useUIStore } from '../store/uiStore'
import { findingColor, nativeFindingColor, type NativeFinding, type PlacementFinding } from '../site3d/placementCheck'
import { componentOfKey } from '../site3d/placementFindingsStore'
import { OUTSIDE_COLOR, WARN_COLOR } from '../site3d/resultStyle'

interface Props {
  findings: readonly PlacementFinding[]
  /** Keys the packer could not arrange by their rules (layout.unresolved). */
  unresolved?: readonly string[]
  /** SC-02: the native scene's findings (a building in the way, a roof); a click selects the item and the building. */
  native?: readonly NativeFinding[]
}

const BUILDING_PREFIX = 'building:'

/** Select both objects a native finding names: the item, and the building it overlaps or stands on. */
function selectNative(f: NativeFinding): void {
  const ui = useUIStore.getState()
  if (f.other?.startsWith(BUILDING_PREFIX)) ui.setSitePartSelection({ kind: 'building', id: f.other.slice(BUILDING_PREFIX.length) })
  ui.setSelectedComponent(componentOfKey(f.key))
}

export default function SiteLayoutFindings({ findings, unresolved = [], native = [] }: Props) {
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const n = findings.length + native.length
  return (
    <details
      data-testid="site-layout-findings"
      // Uncontrolled after mount: the user's toggle rules. A fresh set of
      // findings remounts the element (key), so it opens again when there
      // is something to read.
      key={n === 0 ? 'none' : 'some'}
      open={n > 0}
      className="max-h-[30vh] w-[min(22rem,100%)] overflow-auto rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow"
    >
      <summary className="cursor-pointer select-none">
        <h3 className="inline font-semibold text-text">Layout · {n === 0 ? 'arranged, no findings' : `${n} finding${n === 1 ? '' : 's'}`}</h3>
      </summary>
      {n > 0 && (
        <ul className="mt-1 space-y-0.5 text-muted" aria-label="Site layout findings">
          {findings.map((f, i) => (
            <li key={`${f.key}:${f.kind}:${f.other ?? ''}:${i}`} className="flex items-start gap-1.5">
              <span aria-hidden className="mt-1 inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: findingColor(f.kind) === 'red' ? OUTSIDE_COLOR : WARN_COLOR }} />
              <button
                type="button"
                className="text-left hover:text-text hover:underline"
                title={`Select ${f.key}`}
                onClick={() => setSelectedComponent(componentOfKey(f.key))}
              >
                {f.message}
              </button>
            </li>
          ))}
          {native.map((f, i) => {
            const color = nativeFindingColor(f.kind)
            return (
              <li key={`native:${f.key}:${f.kind}:${f.other ?? ''}:${i}`} className="flex items-start gap-1.5" data-testid="site-native-finding">
                <span aria-hidden className="mt-1 inline-block h-2 w-2 shrink-0 rounded-sm border border-border"
                  style={color ? { background: color === 'red' ? OUTSIDE_COLOR : WARN_COLOR } : undefined} />
                <button type="button" className="text-left hover:text-text hover:underline" onClick={() => selectNative(f)}>
                  {f.message}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {unresolved.length > 0 && (
        <p className="mt-1 text-muted" data-testid="site-layout-unresolved">
          The packer could not satisfy every rule for {unresolved.map(k => componentOfKey(k).name).join(', ')}; move them by hand or loosen the rule.
        </p>
      )}
    </details>
  )
}
