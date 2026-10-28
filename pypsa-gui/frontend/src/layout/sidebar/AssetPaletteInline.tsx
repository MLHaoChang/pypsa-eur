import { useState } from 'react'
import { ChevronDown, ChevronRight, Factory } from 'lucide-react'
import { useUIStore } from '../../store/uiStore'
import { useAssetDrag } from '../../hooks/useAssetDrag'
import { PALETTE_SECTIONS_DATA, PROCESS_UNIT_ITEM } from '../paletteData'
import { PALETTE_ICONS } from '../paletteIcons'

// ── Asset palette sections ─────────────────────────────────────────────────────
// The data (ids, labels, sections) lives in `paletteData.ts`, the icons in
// `paletteIcons.tsx`, keyed by item id.
interface PaletteItem { id: string; label: string; subtitle: string; icon: React.ReactNode }
interface PaletteSection { id: string; label: string; items: PaletteItem[] }


const PALETTE_SECTIONS: PaletteSection[] = PALETTE_SECTIONS_DATA.map(section => ({
  ...section,
  items: section.items.map(item => ({ ...item, icon: PALETTE_ICONS[item.id] })),
}))

/** The palette section that holds demand: it belongs to Loads & demand (4), not to Assets (5). */
export const DEMAND_PALETTE_SECTION = 'demand'

// ── AssetPaletteInline ─────────────────────────────────────────────────────────
export default function AssetPaletteInline({ only, skip }: { only?: string[]; skip?: string[] } = {}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  // The pointer-drag gesture and its drop hit-test live in the hook — it is
  // the single owner of "what is under the cursor", shared by the schematic
  // and map canvases (spec D25). setCreationItem is still needed directly
  // for the keyboard path below (Enter/Space), which is not a drag.
  const { ghost, beginDrag } = useAssetDrag()
  const setCreationItem = useUIStore(s => s.setCreationItem)
  const toggle = (id: string) =>
    setCollapsed(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })

  return (
    <div>
      {PALETTE_SECTIONS.filter(s => (!only || only.includes(s.id)) && !(skip ?? []).includes(s.id)).map(section => (
        <div key={section.id}>
          <button
            onClick={() => toggle(section.id)}
            className="flex items-center gap-1.5 w-full pl-8 pr-3 py-1 hover:bg-panel transition-colors"
          >
            {collapsed.has(section.id)
              ? <ChevronRight size={9} className="text-ink-400 shrink-0" />
              : <ChevronDown  size={9} className="text-ink-400 shrink-0" />}
            <span className="text-[9px] font-bold uppercase tracking-widest text-ink-400">
              {section.label}
            </span>
          </button>
          {!collapsed.has(section.id) && section.items.map(item => (
            // role="button" + tabIndex makes this keyboard-accessible while
            // still allowing pointer-event drag. <button> here would compete
            // with the user-agent's native button mouse handling.
            <div
              key={item.id}
              role="button"
              tabIndex={0}
              className="flex items-center gap-2.5 w-full pl-9 pr-3 py-1 text-left hover:bg-panel-2 transition-colors group cursor-grab active:cursor-grabbing"
              style={{ height: 32 }}
              onPointerDown={(e) => beginDrag(e, { id: item.id, label: item.label })}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setCreationItem({ id: item.id, label: item.label })
                }
              }}
              title={`Click to add ${item.label}, or drag onto the canvas to place at a specific point`}
            >
              <span className="text-muted group-hover:text-text transition-colors shrink-0"
                style={{ width: 15, height: 15, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {item.icon}
              </span>
              <div className="min-w-0">
                <div className="text-[11px] font-medium text-ink-700 truncate">{item.label}</div>
                <div className="text-[9px] text-ink-400 truncate leading-tight">{item.subtitle}</div>
              </div>
            </div>
          ))}
        </div>
      ))}

      {/* The Process unit is a Link built from rates per tonne (grid-to-process 07). Click or Enter only:
          it has no canvas drop. */}
      {(!only || only.includes('process')) && !(skip ?? []).includes('process') && (
        <div data-testid="palette-process">
          <div className="flex items-center gap-1.5 w-full pl-8 pr-3 py-1">
            <span className="text-[9px] font-bold uppercase tracking-widest text-ink-400">PROCESS</span>
          </div>
          <button
            type="button"
            data-testid="palette-process-unit"
            className="flex items-center gap-2.5 w-full pl-9 pr-3 py-1 text-left hover:bg-panel-2 transition-colors group"
            style={{ height: 32 }}
            onClick={() => setCreationItem({ id: PROCESS_UNIT_ITEM.id, label: PROCESS_UNIT_ITEM.label })}
            title="Add a process unit: enter rates per tonne of product and the efficiencies are derived"
          >
            <span className="text-muted group-hover:text-text transition-colors shrink-0"
              style={{ width: 15, height: 15, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Factory size={15} />
            </span>
            <div className="min-w-0">
              <div className="text-[11px] font-medium text-ink-700 truncate">{PROCESS_UNIT_ITEM.label}</div>
              <div className="text-[9px] text-ink-400 truncate leading-tight">{PROCESS_UNIT_ITEM.subtitle}</div>
            </div>
          </button>
        </div>
      )}

      {/* Drag ghost — z-[9999] beats every panel; pointer-events-none lets
          the pointerup land on the real target underneath. */}
      {ghost && (
        <div
          className="fixed z-[9999] pointer-events-none flex items-center gap-1.5 px-2 py-1 bg-accent text-white text-[11px] font-medium rounded shadow-lg"
          style={{ left: ghost.x + 12, top: ghost.y + 12 }}
        >
          {ghost.label}
        </div>
      )}
    </div>
  )
}
