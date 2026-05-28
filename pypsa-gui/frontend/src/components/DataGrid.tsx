import { useState, useCallback, useRef } from 'react'
import {
  useReactTable, getCoreRowModel, getSortedRowModel, getFilteredRowModel,
  flexRender, type ColumnDef, type SortingState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Trash2, Plus, Search } from 'lucide-react'

interface DataGridProps<T extends Record<string, unknown>> {
  columns: ColumnDef<T>[]
  data: T[]
  onUpdate?: (rowIndex: number, columnId: string, value: unknown) => void
  onDelete?: (row: T) => void
  onAdd?: () => void
  getRowId?: (row: T) => string
  isLoading?: boolean
}

export function DataGrid<T extends Record<string, unknown>>({
  columns, data, onUpdate, onDelete, onAdd, isLoading,
}: DataGridProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({})
  const [editCell, setEditCell] = useState<{ rowIdx: number; colId: string; value: string } | null>(null)
  const parentRef = useRef<HTMLDivElement>(null)

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, rowSelection },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    enableRowSelection: true,
  })

  const { rows } = table.getRowModel()

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 32,
    overscan: 10,
  })

  const handleCellClick = useCallback((rowIdx: number, colId: string, value: unknown) => {
    if (!onUpdate) return
    setEditCell({ rowIdx, colId, value: String(value ?? '') })
  }, [onUpdate])

  const commitEdit = useCallback(() => {
    if (!editCell || !onUpdate) return
    onUpdate(editCell.rowIdx, editCell.colId, editCell.value)
    setEditCell(null)
  }, [editCell, onUpdate])

  const totalSize = virtualizer.getTotalSize()
  const virtualItems = virtualizer.getVirtualItems()

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-2 py-1.5 border-b border-border shrink-0">
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            className="bg-bg border border-border rounded pl-6 pr-2 py-1 text-xs text-text placeholder-muted focus:outline-none focus:border-accent w-44"
            placeholder="Search…"
            value={globalFilter}
            onChange={e => setGlobalFilter(e.target.value)}
          />
        </div>
        {onAdd && (
          <button
            onClick={onAdd}
            className="flex items-center gap-1 px-2 py-1 bg-accent/20 text-accent rounded text-xs hover:bg-accent/30 transition-colors"
          >
            <Plus size={12} /> Add
          </button>
        )}
        {onDelete && Object.keys(rowSelection).length > 0 && (
          <button
            onClick={() => {
              Object.keys(rowSelection).forEach(idx => {
                const row = rows[parseInt(idx)]
                if (row) onDelete(row.original as T)
              })
              setRowSelection({})
            }}
            className="flex items-center gap-1 px-2 py-1 bg-danger/20 text-danger rounded text-xs hover:bg-danger/30 transition-colors"
          >
            <Trash2 size={12} /> Delete ({Object.keys(rowSelection).length})
          </button>
        )}
        <span className="ml-auto text-xs text-muted">{rows.length} rows</span>
      </div>

      {/* Table */}
      <div ref={parentRef} className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-8 text-center text-muted text-sm animate-pulse">Loading…</div>
        ) : (
          <table className="w-full text-xs border-collapse min-w-max">
            <thead className="sticky top-0 z-10 bg-panel">
              {table.getHeaderGroups().map(hg => (
                <tr key={hg.id}>
                  {hg.headers.map(header => (
                    <th
                      key={header.id}
                      className="px-2 py-1.5 text-left text-muted font-semibold border-b border-border cursor-pointer select-none hover:text-text whitespace-nowrap"
                      onClick={header.column.getToggleSortingHandler()}
                      style={{ width: header.getSize() }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getIsSorted() === 'asc' ? ' ↑' : header.column.getIsSorted() === 'desc' ? ' ↓' : ''}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody style={{ height: totalSize }}>
              {virtualItems.map(vi => {
                const row = rows[vi.index]
                return (
                  <tr
                    key={row.id}
                    data-index={vi.index}
                    ref={virtualizer.measureElement}
                    className={`border-b border-border/40 ${vi.index % 2 === 0 ? 'bg-bg' : 'bg-panel/60'} hover:bg-accent/5 transition-colors`}
                  >
                    {row.getVisibleCells().map(cell => {
                      const isEditing = editCell?.rowIdx === vi.index && editCell?.colId === cell.column.id
                      const val = cell.getValue()
                      return (
                        <td
                          key={cell.id}
                          className="px-2 py-1 text-text whitespace-nowrap"
                          onDoubleClick={() => handleCellClick(vi.index, cell.column.id, val)}
                        >
                          {isEditing ? (
                            <input
                              autoFocus
                              className="bg-bg border border-accent rounded px-1 py-0.5 text-xs w-full font-mono focus:outline-none"
                              value={editCell!.value}
                              onChange={e => setEditCell(prev => prev ? { ...prev, value: e.target.value } : null)}
                              onBlur={commitEdit}
                              onKeyDown={e => {
                                if (e.key === 'Enter') commitEdit()
                                if (e.key === 'Escape') setEditCell(null)
                              }}
                            />
                          ) : (
                            <span className={typeof val === 'number' ? 'font-mono' : ''}>
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="p-8 text-center text-muted text-sm">
            No data yet. {onAdd && <button onClick={onAdd} className="text-accent underline">Add the first entry</button>}
          </div>
        )}
      </div>
    </div>
  )
}
