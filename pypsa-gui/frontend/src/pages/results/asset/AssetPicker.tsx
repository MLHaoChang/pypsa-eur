import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Search } from 'lucide-react'
import type { AssetRef } from './types'

// Canonical display order — mirrors registry.ALL_CLASSES.
const CLASS_ORDER = ['Bus', 'Generator', 'Load', 'Line', 'Transformer',
  'Link', 'StorageUnit', 'Store'] as const

export function filterAssets(assets: AssetRef[], query: string): AssetRef[] {
  const q = query.trim().toLowerCase()
  if (!q) return assets
  return assets.filter(a =>
    a.name.toLowerCase().includes(q) || a.carrier.toLowerCase().includes(q))
}

export function groupByClass(assets: AssetRef[]): Array<[string, AssetRef[]]> {
  return CLASS_ORDER
    .map(cls => [cls, assets.filter(a => a.class === cls)] as [string, AssetRef[]])
    .filter(([, rows]) => rows.length > 0)
}

/** Flatten groups into a single virtualisable row list: headers + assets. */
type Row = { kind: 'header'; label: string } | { kind: 'asset'; asset: AssetRef }

export default function AssetPicker(
  { assets, selected, onSelect }: {
    assets: AssetRef[]; selected: AssetRef | null; onSelect: (a: AssetRef) => void
  },
) {
  const [query, setQuery] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => filterAssets(assets, query), [assets, query])
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const [cls, group] of groupByClass(filtered)) {
      out.push({ kind: 'header', label: `${cls} (${group.length})` })
      for (const asset of group) out.push({ kind: 'asset', asset })
    }
    return out
  }, [filtered])

  // A 5 000-asset network must not render 5 000 DOM nodes.
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 24,
    overscan: 12,
  })

  return (
    <div className="flex flex-col h-full min-h-0 border-r border-border bg-panel">
      <div className="shrink-0 p-2 border-b border-border">
        <div className="flex items-center gap-1.5 px-2 h-7 border border-border rounded bg-bg">
          <Search size={12} className="text-muted" />
          <input
            type="search"
            role="searchbox"
            aria-label="Search assets"
            value={query}
            placeholder="Search assets…"
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                const first = rows.find(r => r.kind === 'asset')
                if (first && first.kind === 'asset') onSelect(first.asset)
              } else if (e.key === 'Escape') {
                setQuery('')
                ;(e.target as HTMLInputElement).blur()
              }
            }}
            className="flex-1 bg-transparent text-[11px] outline-none"
          />
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="p-3 text-[11px] text-muted">No assets match “{query}”.</p>
        ) : (
          <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
            {virt.getVirtualItems().map(v => {
              const row = rows[v.index]
              const style: React.CSSProperties = {
                position: 'absolute', top: 0, left: 0, width: '100%',
                height: v.size, transform: `translateY(${v.start}px)`,
              }
              if (row.kind === 'header') {
                return (
                  <div key={v.key} style={style}
                    className="px-2 flex items-center text-[9px] uppercase tracking-wider text-muted bg-panel">
                    {row.label}
                  </div>
                )
              }
              const a = row.asset
              const active = selected?.class === a.class && selected?.name === a.name
              return (
                <button
                  key={v.key} style={style}
                  aria-current={active ? 'true' : undefined}
                  onClick={() => onSelect(a)}
                  title={a.carrier ? `${a.name} · ${a.carrier}` : a.name}
                  className={`px-2 pl-4 flex items-center gap-1.5 text-left text-[11px] truncate
                    ${active ? 'bg-accent/15 text-accent' : 'text-text hover:bg-border/40'}`}
                >
                  <span className="truncate">{a.name}</span>
                  {a.carrier && (
                    <span className="text-[9px] text-muted truncate">{a.carrier}</span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
