import { useState, useRef, useEffect, useId } from 'react'

interface BusAutocompleteProps {
  value: string
  onChange: (v: string) => void
  buses: string[]
  placeholder?: string
  required?: boolean
  readOnly?: boolean
}

export default function BusAutocomplete({
  value, onChange, buses, placeholder = 'Select bus…', required, readOnly,
}: BusAutocompleteProps) {
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const uid = useId()

  const filtered = buses
    .filter(b => b.toLowerCase().includes(value.toLowerCase()))
    .slice(0, 60)
  const exactMatch = buses.some(b => b.toLowerCase() === value.toLowerCase())
  const showWarn = value.trim().length > 0 && !exactMatch

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const select = (name: string) => {
    onChange(name)
    setOpen(false)
    setCursor(-1)
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (readOnly) return
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') { setOpen(true); setCursor(0) }
      return
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor(c => Math.min(c + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor(c => Math.max(c - 1, -1)) }
    else if (e.key === 'Enter') { if (cursor >= 0 && filtered[cursor]) { e.preventDefault(); select(filtered[cursor]) } }
    else if (e.key === 'Escape') { setOpen(false); setCursor(-1) }
  }

  useEffect(() => {
    if (cursor < 0 || !listRef.current) return
    const el = listRef.current.children[cursor] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const [dropPos, setDropPos] = useState({ top: 0, left: 0, width: 0 })
  useEffect(() => {
    if (!open || !inputRef.current) return
    const rect = inputRef.current.getBoundingClientRect()
    setDropPos({ top: rect.bottom + 4, left: rect.left, width: rect.width + 28 })
  }, [open, value])

  const base = 'bg-bg border border-border px-2 py-1.5 text-xs focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

  return (
    <div ref={wrapRef} className="relative">
      <div className="flex">
        <input
          ref={inputRef}
          id={uid}
          type="text"
          value={value}
          required={required}
          readOnly={readOnly}
          placeholder={placeholder}
          onChange={e => { if (!readOnly) { onChange(e.target.value); setOpen(true); setCursor(-1) } }}
          onFocus={() => { if (!readOnly) setOpen(true) }}
          onKeyDown={handleKey}
          className={`flex-1 rounded-l ${base} ${readOnly ? 'bg-panel cursor-not-allowed text-muted' : ''}`}
        />
        {!readOnly && (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => { setOpen(o => !o); inputRef.current?.focus() }}
            aria-label="Show bus suggestions"
            className="px-1.5 border border-l-0 border-border rounded-r text-muted hover:text-text hover:border-accent transition-colors"
          >
            <svg width="10" height="7" viewBox="0 0 10 7" fill="none">
              <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}
        {readOnly && (
          <span className="px-1.5 border border-l-0 border-border rounded-r flex items-center text-muted bg-panel">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.4">
              <rect x="2.5" y="5" width="6" height="5" rx="1"/>
              <path d="M4 5V3.5a1.5 1.5 0 1 1 3 0V5"/>
            </svg>
          </span>
        )}
      </div>
      {showWarn && !readOnly && (
        <p className="text-[9px] text-warn mt-0.5 leading-tight">
          No bus with this name — it will be created automatically
        </p>
      )}
      {open && !readOnly && filtered.length > 0 && (
        <ul
          ref={listRef}
          style={{ position: 'fixed', top: dropPos.top, left: dropPos.left, width: dropPos.width, zIndex: 9500, maxHeight: 200, overflowY: 'auto' }}
          className="bg-bg border border-border rounded shadow-lg py-0.5"
        >
          {filtered.map((b, i) => (
            <li
              key={b}
              onMouseDown={() => select(b)}
              className={`px-2.5 py-1.5 text-xs cursor-pointer transition-colors ${i === cursor ? 'bg-accent/10 text-accent' : 'text-text hover:bg-border/30'}`}
            >
              {b}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
