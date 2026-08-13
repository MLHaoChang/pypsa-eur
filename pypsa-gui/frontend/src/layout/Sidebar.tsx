import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ChevronLeft, ChevronRight, ChevronDown,
  FolderOpen, Layers, Settings2, TrendingUp,
  FilePlus, Save, Copy, BookOpen, Upload, FileDown,
  BarChart2, Download, X, Check, Clock,
  Flame, Wind, BatteryCharging, Droplets,
  MousePointer, ZoomIn, ZoomOut, AlertTriangle,
  Thermometer, Zap, Camera, LayoutDashboard,
  Sun, Moon, Rows2, Rows3,
  GitBranch as GitBranchIcon, ListChecks,
  MessageSquare, LayoutGrid, Users, SlidersHorizontal,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useUIStore } from '../store/uiStore'
import { useAssetDrag } from '../hooks/useAssetDrag'
import { networkApi } from '../api/network'
import { ioApi } from '../api/io'
import { projectsApi } from '../api/projects'
import { simulationApi } from '../api/simulation'
import type { ImportSummary, ProjectInfo } from '../api/types'
import { ImportZone } from '../pages/ImportExport'
import { appLog, useSimulationStore } from '../store/simulationStore'
import { formatApiDetail } from '../api/client'
import toast from 'react-hot-toast'
import {
  invalidateNetworkQueries, saveProjectQuietly,
  resetBackendNetwork, slugify as slugifyName, downloadProjectBundle,
  formatRelativeTime, abortRunningSim, switchToProject,
} from '../utils/projectActions'
import { nk } from '../utils/queryKeys'
import NewProjectWizard from './NewProjectWizard'
import { H2Icon } from '../components/AssetIcons'
import ProjectPicker from '../components/ProjectPicker'
import { useSolveQueue } from '../hooks/useSolveQueue'
import { useLocalSettingsAvailable } from '../hooks/useLocalSettings'
import { isActive } from '../api/solveQueue'
import { evaluateMutation } from '../utils/mutationGuard'
import { flushPendingEdgeDeletes } from '../utils/pendingEdgeDeletes'

// ── Constants ──────────────────────────────────────────────────────────────────
const SIDEBAR_EXPANDED_W = 240
const SIDEBAR_ICON_W = 48

const slugify = slugifyName

// ── Inline SVG icons ───────────────────────────────────────────────────────────
const BusIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none">
    <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="10" cy="10" r="2.5" fill="currentColor" />
  </svg>
)
const LineIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <line x1="2" y1="10" x2="18" y2="10" />
    <line x1="6" y1="7" x2="6" y2="13" />
    <line x1="10" y1="7" x2="10" y2="13" />
    <line x1="14" y1="7" x2="14" y2="13" />
  </svg>
)
const FlywheelIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="10" cy="10" r="7" />
    <circle cx="10" cy="10" r="2.5" />
    <path d="M10 3 Q14 6 17 10" strokeLinecap="round" />
  </svg>
)
const CAESIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M4 12 Q7 8 10 12 Q13 16 16 12" />
    <path d="M4 8 Q7 4 10 8 Q13 12 16 8" />
  </svg>
)
const ElectrolyzerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M2 10 L7 10" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M13 10 L18 10" strokeDasharray="2 1.5" />
    <text x="8.5" y="14" fontSize="5" fontWeight="700" fill="currentColor" stroke="none" fontFamily="monospace">H₂</text>
  </svg>
)
const FuelCellIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M2 10 L7 10" strokeDasharray="2 1.5" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M13 10 L18 10" />
    <path d="M10 7.5 L10 8.5 M9 8 L11 8" strokeWidth="1.2" />
    <path d="M9 11 L11 13 M11 11 L9 13" strokeWidth="1.2" />
  </svg>
)
// Heat pump / resistive heater: electricity in (left) → heat out (squiggle right).
const PowerToHeatIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <path d="M2 10 L7 10" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M14 6 q2 2 0 4 q-2 2 0 4" />
    <path d="M16.5 6 q2 2 0 4 q-2 2 0 4" />
  </svg>
)
// CHP: fuel in → both electricity + heat out.
const CHPIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <path d="M2 10 L6 10" />
    <rect x="6" y="5" width="6" height="10" rx="1.5" />
    <path d="M12 7.5 L17 5.5" />
    <path d="M12 12.5 q2 0.5 3.5 2 q-1 -0.5 -1.5 1.5 q1.5 -1 3 0.5" />
  </svg>
)
// Thermal storage: tank with wave inside.
const ThermalStorageIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <rect x="4" y="3" width="12" height="14" rx="2" />
    <path d="M5.5 11 q2 -1.5 4 0 t 4 0 t 1.5 0" />
    <path d="M5.5 8 q2 -1.5 4 0 t 4 0 t 1.5 0" strokeOpacity="0.6" />
  </svg>
)
// Transformer: classic IEC two-interlocking-circles symbol.
const TransformerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <circle cx="7" cy="10" r="4" />
    <circle cx="13" cy="10" r="4" />
  </svg>
)

// ── Asset palette sections ─────────────────────────────────────────────────────
interface PaletteItem { id: string; label: string; subtitle: string; icon: React.ReactNode }
interface PaletteSection { id: string; label: string; items: PaletteItem[] }

const PALETTE_SECTIONS: PaletteSection[] = [
  {
    id: 'network', label: 'NETWORK',
    items: [
      { id: 'bus',         label: 'Bus (Node)',        subtitle: 'Voltage / carrier node', icon: <BusIcon /> },
      { id: 'line',        label: 'Transmission Line', subtitle: 'AC line / branch',       icon: <LineIcon /> },
      { id: 'transformer', label: 'Transformer',       subtitle: 'Voltage step (e.g. 380/220)', icon: <TransformerIcon /> },
    ],
  },
  {
    id: 'electricity', label: 'ELECTRICITY',
    items: [
      { id: 'thermal',   label: 'Conventional', subtitle: 'Coal, CCGT, oil, gas…', icon: <Flame size={14} /> },
      { id: 'renewable', label: 'Renewable',    subtitle: 'Wind, solar, hydro',    icon: <Wind size={14} /> },
    ],
  },
  {
    id: 'hydrogen', label: 'HYDROGEN',
    items: [
      { id: 'electrolyzer', label: 'Electrolyzer', subtitle: 'Electricity → H₂', icon: <ElectrolyzerIcon /> },
      { id: 'fuel_cell',    label: 'Fuel Cell',    subtitle: 'H₂ → Electricity', icon: <FuelCellIcon /> },
    ],
  },
  {
    id: 'heat', label: 'HEAT',
    items: [
      { id: 'power_to_heat', label: 'Power-to-Heat', subtitle: 'Heat pump / resistive heater', icon: <PowerToHeatIcon /> },
      { id: 'chp',           label: 'CHP Plant',     subtitle: 'Co-generation (elec + heat)', icon: <CHPIcon /> },
    ],
  },
  {
    id: 'storage', label: 'STORAGE',
    items: [
      { id: 'battery',         label: 'Battery',          subtitle: 'Li-Ion / BESS',       icon: <BatteryCharging size={14} /> },
      { id: 'psh',             label: 'Pumped Hydro',     subtitle: 'Large-scale PSH',     icon: <Droplets size={14} /> },
      { id: 'caes',            label: 'Compressed Air',   subtitle: 'CAES',                icon: <CAESIcon /> },
      { id: 'flywheel',        label: 'Flywheel',         subtitle: 'Short-duration',      icon: <FlywheelIcon /> },
      { id: 'hydrogen',        label: 'Hydrogen Storage', subtitle: 'H₂ tank / cavern',    icon: <H2Icon /> },
      { id: 'thermal_storage', label: 'Thermal Storage',  subtitle: 'Hot-water tank / TES', icon: <ThermalStorageIcon /> },
    ],
  },
  {
    id: 'demand', label: 'DEMAND',
    items: [
      { id: 'load_elec', label: 'Electrical Demand', subtitle: 'Load on AC/DC bus', icon: <Zap size={14} /> },
      { id: 'load_h2',   label: 'Hydrogen Demand',   subtitle: 'Load on H₂ bus',    icon: <H2Icon /> },
      { id: 'load_heat', label: 'Heating Demand',    subtitle: 'Load on heat bus',  icon: <Thermometer size={14} /> },
    ],
  },
]

// ── Shared sidebar item row ────────────────────────────────────────────────────
function SItem({
  icon, label, hint, rightEl, onClick, active, title,
}: {
  icon: React.ReactNode; label: string; hint?: string; rightEl?: React.ReactNode
  onClick?: () => void; active?: boolean; title?: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex items-center gap-3 w-full pl-6 pr-3 transition-colors text-left"
      style={{
        height: 36,
        background: active ? 'var(--color-panel-2)' : undefined,
        borderLeft: active ? '3px solid var(--color-accent)' : '3px solid transparent',
      }}
      onMouseEnter={e => { if (!active) (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' }}
      onMouseLeave={e => { if (!active) (e.currentTarget as HTMLElement).style.background = '' }}
    >
      <span className="shrink-0 text-ink-600" style={{ width: 16, display: 'flex', alignItems: 'center' }}>
        {icon}
      </span>
      <span className="flex-1 text-[13px] font-medium text-text truncate min-w-0">{label}</span>
      {hint && <span className="text-[10px] text-ink-400 font-mono ml-1 max-w-[6rem] truncate shrink">{hint}</span>}
      {rightEl}
    </button>
  )
}

function SectionHdr({ title, open, onToggle }: { title: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className="flex items-center justify-between w-full px-4 py-2 mt-1 text-left hover:bg-panel transition-colors"
    >
      <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-ink-400">{title}</span>
      <ChevronDown size={11} className="text-ink-400 transition-transform duration-200 shrink-0"
        style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
    </button>
  )
}

// SubHdr is the smaller, non-collapsible group label used inside a SectionHdr's
// expanded body (e.g. "Quick actions", "Recent", "Workspace"). Visual hierarchy:
// SectionHdr (bold, 10px) → SubHdr (medium, 9px) → SItem rows.
function SubHdr({ title }: { title: string }) {
  return (
    <div className="px-6 pt-3 pb-1 text-[9px] font-semibold uppercase tracking-[0.08em] text-ink-400">
      {title}
    </div>
  )
}

// ── AssetPaletteInline ─────────────────────────────────────────────────────────
function AssetPaletteInline() {
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
      {PALETTE_SECTIONS.map(section => (
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

// ── IO Modal ───────────────────────────────────────────────────────────────────
function IoModal({ onClose, initialTab = 'import', projectName }: {
  onClose: () => void; initialTab?: 'import' | 'export'; projectName: string
}) {
  const [tab, setTab] = useState<'import' | 'export'>(initialTab)
  const [exportFmt, setExportFmt] = useState<'bundle' | 'netcdf' | 'csv' | 'excel' | 'matpower'>('bundle')
  const [exportFilename, setExportFilename] = useState(slugify(projectName))
  const [exporting, setExporting] = useState(false)
  const qc = useQueryClient()
  const { setProjectName, currentProject } = useUIStore()

  const handleImportSuccess = (summary: ImportSummary, fileName?: string) => {
    // Scope to the active project's network + results (the import replaced the
    // in-memory network bound to currentProject) rather than a bare
    // `invalidateQueries()` that nuked every resident project's cache. Other
    // open projects' caches must survive for B8 instant-switch.
    invalidateNetworkQueries(qc, currentProject)
    qc.invalidateQueries({ queryKey: nk(currentProject, 'results') })
    // For .pypsaproj.zip bundles, ImportZone already set the canonical project
    // name (matches the backend project key) — don't clobber it here.
    if (fileName && !fileName.toLowerCase().endsWith('.pypsaproj.zip')) {
      // Strip the compound ".pypsaproj.zip" first (in case it slips through),
      // then any remaining single extension (.nc/.xlsx/.zip/.m). Preserve
      // underscores — they belong to the project name as the user typed it.
      const cleaned = fileName
        .replace(/\.pypsaproj\.zip$/i, '')
        .replace(/\.[^.]+$/, '')
        .trim()
      if (cleaned) setProjectName(cleaned)
    }
    onClose()
    const msg = `Network imported${fileName ? ` (${fileName})` : ''}: ${summary.buses} buses, ${summary.generators} generators, ${summary.lines} lines, ${summary.snapshots} snapshots`
    toast.success(`Imported: ${summary.buses} buses · ${summary.generators} generators · ${summary.snapshots} snapshots`)
    appLog('INFO', msg)
  }

  const handleExport = async () => {
    setExporting(true)
    try {
      let blob: Blob
      let ext: string
      if (exportFmt === 'bundle') {
        if (!currentProject) {
          toast.error('Save the project first, then export as bundle.')
          return
        }
        blob = await projectsApi.downloadBundle(currentProject)
        ext = '.pypsaproj.zip'
      } else {
        const fns = { netcdf: ioApi.exportNetcdf, csv: ioApi.exportCsv, excel: ioApi.exportExcel, matpower: ioApi.exportMatpower }
        const exts = { netcdf: '.nc', csv: '.zip', excel: '.xlsx', matpower: '.m' }
        blob = await fns[exportFmt]()
        ext = exts[exportFmt]
      }
      const fname = (exportFilename.trim() || slugify(projectName)) + ext
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = fname; a.click()
      URL.revokeObjectURL(url)
      toast.success(`Downloaded ${fname}`)
    } catch { toast.error('Export failed') } finally { setExporting(false) }
  }

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-bg rounded-xl shadow-2xl w-[480px] max-w-[95vw] overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
          <div>
            <p className="text-[9px] font-bold text-accent uppercase tracking-widest">NETWORK</p>
            <p className="text-sm font-semibold text-text">Project Data</p>
          </div>
          <button onClick={onClose} className="p-1 text-muted hover:text-text transition-colors"><X size={16} /></button>
        </div>
        <div className="flex border-b border-border px-5">
          {(['import', 'export'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-3 py-2.5 text-[12px] font-medium border-b-2 -mb-px capitalize transition-colors
                ${tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'}`}>
              {t === 'import' ? 'Import' : 'Export'}
            </button>
          ))}
        </div>
        <div className="p-5">
          {tab === 'import' && (
            <>
              <ImportZone onSuccess={handleImportSuccess} />
              <p className="mt-3 text-[10px] text-muted text-center">
                Recommended: <span className="font-mono">.pypsaproj.zip</span> (network + results).
                Also: <span className="font-mono">.nc</span> ·{' '}
                <span className="font-mono">.xlsx</span> ·{' '}
                <span className="font-mono">.zip</span> ·{' '}
                <span className="font-mono">.m</span> (network only)
              </p>
            </>
          )}
          {tab === 'export' && (
            <>
              <label className="flex flex-col gap-1 mb-4">
                <span className="text-xs font-medium text-text">File name</span>
                <div className="flex items-center gap-1">
                  <input
                    type="text"
                    value={exportFilename}
                    onChange={e => setExportFilename(e.target.value)}
                    placeholder={slugify(projectName)}
                    className="flex-1 px-2.5 py-1.5 text-xs border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
                  />
                  <span className="text-[10px] text-muted font-mono">
                    {exportFmt === 'bundle' ? '.pypsaproj.zip' : exportFmt === 'netcdf' ? '.nc' : exportFmt === 'csv' ? '.zip' : exportFmt === 'excel' ? '.xlsx' : '.m'}
                  </span>
                </div>
              </label>
              <p className="text-xs text-muted mb-3">Format:</p>
              <div className="space-y-2 mb-4">
                {([
                  { id: 'bundle',   label: 'Project bundle (.pypsaproj.zip)', desc: 'Network + solve results + time series + solver config + layout — full round-trip' },
                  { id: 'netcdf',   label: 'NetCDF (.nc)',   desc: 'Native PyPSA format — lossless' },
                  { id: 'excel',    label: 'Excel (.xlsx)',  desc: 'One sheet per component' },
                  { id: 'csv',      label: 'CSV zip (.zip)', desc: 'Folder of CSVs' },
                  { id: 'matpower', label: 'MATPOWER (.m)',  desc: 'AC-only, for MATLAB/Octave' },
                ] as const).map(f => (
                  <label key={f.id} className="flex items-center gap-3 cursor-pointer p-2.5 rounded-lg border border-border hover:border-accent/50 transition-colors">
                    <input type="radio" name="fmt" value={f.id} checked={exportFmt === f.id}
                      onChange={() => setExportFmt(f.id)} className="accent-accent" />
                    <div>
                      <div className="text-xs font-semibold text-text">{f.label}</div>
                      <div className="text-[10px] text-muted">{f.desc}</div>
                    </div>
                  </label>
                ))}
              </div>
              <p className="text-[10px] text-muted mb-4 flex items-start gap-1.5">
                <span className="shrink-0 mt-0.5">ⓘ</span>
                Files are downloaded to your browser's default download folder. To change the folder, update your browser settings.
              </p>
              <div className="flex gap-2 justify-end">
                <button onClick={onClose} className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">Cancel</button>
                <button onClick={handleExport} disabled={exporting}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 disabled:opacity-50 transition-colors">
                  <Download size={12} /> {exporting ? 'Downloading…' : 'Download File'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Open project modal ─────────────────────────────────────────────────────────
// Two ways to open a project:
//   1. Pick from the backend's saved-projects list (recent / persistent state).
//   2. Browse to a .pypsaproj.zip on disk — the bundle is uploaded, the backend
//      registers it as a new project under its metadata.json name, and it
//      opens as a new tab.
// Both paths auto-save the active project before switching so unsaved edits
// survive the swap.
function OpenProjectModal({
  currentProject, onPick, onPickFile, onClose,
}: {
  currentProject: string | null
  onPick: (name: string) => void
  onPickFile: (file: File) => void
  onClose: () => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-bg rounded-xl shadow-2xl w-[480px] max-h-[80vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <span className="text-sm font-semibold text-text">Open project</span>
          <button onClick={onClose} className="p-1 text-muted hover:text-text"><X size={15} /></button>
        </div>
        {/* Browse from disk — works for projects exported as .pypsaproj.zip
            from another machine or backed up outside the projects folder. */}
        <div className="px-4 py-3 border-b border-border shrink-0">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 border border-dashed border-border rounded text-xs text-muted hover:text-accent hover:border-accent hover:bg-accent/5 transition-colors"
          >
            <Upload size={12} />
            <span>Browse for a project file (.pypsaproj.zip)</span>
          </button>
          <p className="mt-1.5 text-[10px] text-muted text-center">
            Full project file: network + results + config. Opens as a project tab.
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pypsaproj.zip,.zip"
            className="hidden"
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) onPickFile(f)
              e.target.value = ''
            }}
          />
        </div>
        <div className="px-4 pt-2 shrink-0">
          <p className="text-[10px] font-bold text-muted uppercase tracking-wider">Saved on backend</p>
        </div>
        {/* Shared with the project-tab strip's "+" dialog, so both lists carry
            the same filter, badges and empty states. */}
        <ProjectPicker currentProject={currentProject} onPick={onPick} />
      </div>
    </div>
  )
}

// ── Save name prompt modal ─────────────────────────────────────────────────────
function SaveNameModal({
  initial, onSave, onClose,
}: {
  initial: string; onSave: (name: string) => void; onClose: () => void
}) {
  const [name, setName] = useState(initial)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select() }, [])
  const commit = () => { const n = name.trim(); if (n) onSave(n) }
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-bg rounded-xl shadow-2xl w-80 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-sm font-semibold text-text">Save project</span>
          <button onClick={onClose} className="p-1 text-muted hover:text-text"><X size={15} /></button>
        </div>
        <div className="p-4 space-y-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text">Project name</span>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') onClose() }}
              className="px-2.5 py-1.5 text-xs border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
              placeholder="my_project"
            />
            <span className="text-[10px] text-muted">Saved to the backend projects folder</span>
          </label>
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">Cancel</button>
            <button
              onClick={commit}
              disabled={!name.trim()}
              className="px-4 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 disabled:opacity-40 transition-colors"
            >Save</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Section content components (used in both expanded and flyout) ──────────────
// Header card that sits at the top of the expanded PROJECT section. Shows
// the active project's name + dirty dot + last-saved timestamp + autosave
// indicator. Falls back to an empty-state hint when no project is loaded.
function ProjectHeaderCard({
  currentProject, lastSavedIso, dirty, autosaveEnabled, onToggleAutosave,
  onRename,
}: {
  currentProject: string | null
  lastSavedIso: string | null
  dirty: boolean
  autosaveEnabled: boolean
  onToggleAutosave: () => void
  onRename: () => void
}) {
  // Tick once per minute so "Saved Xm ago" updates without polling the
  // backend. 60 s granularity is fine — the label only changes minute-to-minute.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])

  if (!currentProject) {
    return (
      <div className="mx-3 mt-2 mb-2 p-3 rounded border border-[var(--color-border)] bg-panel">
        <div className="text-[12px] font-semibold text-ink-600 mb-0.5">No project loaded</div>
        <div className="text-[10px] text-ink-400 leading-relaxed">
          Start with New Project below, or open a recent one.
        </div>
      </div>
    )
  }

  void tick  // referenced to keep the per-minute rerender from being optimised out
  const relative = formatRelativeTime(lastSavedIso)
  const savedLine = relative
    ? (dirty ? `Unsaved · last save ${relative}` : `Saved ${relative}`)
    : (dirty ? 'Unsaved changes' : 'Saved')

  return (
    <div
      className="mx-3 mt-2 mb-2 p-3 rounded border bg-panel"
      style={{
        borderColor: dirty ? 'rgba(217,119,6,0.35)' : 'var(--color-border)',
      }}
    >
      <button
        type="button"
        onClick={onRename}
        title="Rename project (header field)"
        className="flex items-center gap-1.5 w-full text-left group"
      >
        <span
          aria-hidden
          className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: dirty ? '#d97706' : '#16a34a' }}
        />
        <span className="text-[13px] font-semibold text-text truncate min-w-0 group-hover:underline">
          {currentProject}
        </span>
      </button>
      <div className="text-[10px] text-muted mt-1 leading-relaxed flex items-center gap-1.5">
        <span className="truncate">{savedLine}</span>
      </div>
      <button
        type="button"
        onClick={onToggleAutosave}
        className="flex items-center gap-1.5 mt-2 text-[10px] text-muted hover:text-ink-700 transition-colors"
        title="Toggle autosave (5 min)"
      >
        <span
          className="shrink-0"
          style={{
            width: 24, height: 13, borderRadius: 7,
            background: autosaveEnabled ? 'var(--color-accent)' : 'var(--color-border-2)',
            display: 'inline-flex', alignItems: 'center', padding: '0 2px',
            transition: 'background 200ms',
          }}
        >
          <span style={{
            width: 9, height: 9, borderRadius: '50%', background: '#fff',
            transform: autosaveEnabled ? 'translateX(11px)' : 'translateX(0)',
            transition: 'transform 200ms', display: 'block',
          }} />
        </span>
        Autosave {autosaveEnabled ? 'on · 5 min' : 'off'}
      </button>
    </div>
  )
}

function ProjectSectionContent({
  onCloseModal, projectName, saveStatus, setSaveStatus,
  openIo, handleNewProject,
}: {
  onCloseModal?: () => void
  projectName: string
  saveStatus: 'idle' | 'saved'
  setSaveStatus: (s: 'idle' | 'saved') => void
  openIo: (tab: 'import' | 'export') => void
  handleNewProject: () => void
}) {
  const {
    projectName: pn, currentProject, setCurrentProject, setProjectName,
    autosaveEnabled, setAutosaveEnabled, markProjectSaved,
    lastSavedByProject, recents,
    activeSlidePanel, setSlidePanel, setProjectSwitchInProgress,
    readOnly, readOnlyReason,
  } = useUIStore()
  const [showNameModal, setShowNameModal] = useState(false)
  // Separate flag for the "Save a Copy" flow so its modal can pre-fill a
  // distinct default name and the confirm handler doesn't switch the active
  // project to the copy.
  const [showCopyModal, setShowCopyModal] = useState(false)
  // "Open Project" picker — lists existing backend projects.
  const [showOpenModal, setShowOpenModal] = useState(false)
  // Route back to the projects home (the workspace surface that now owns
  // create / import / duplicate).
  const navigate = useNavigate()

  // Phase D polish #1 — chat empty-state primer bridge. The chat panel
  // dispatches CustomEvents instead of reaching across the layout tree;
  // this Sidebar effect listens and opens the existing modals. Decoupled
  // so chat panel doesn't depend on Sidebar's local state shape.
  useEffect(() => {
    const onNewProject = () => handleNewProject()
    const onOpenPicker = () => setShowOpenModal(true)
    window.addEventListener('chat:open-new-project-wizard', onNewProject)
    window.addEventListener('chat:open-project-picker', onOpenPicker)
    return () => {
      window.removeEventListener('chat:open-new-project-wizard', onNewProject)
      window.removeEventListener('chat:open-project-picker', onOpenPicker)
    }
  }, [handleNewProject])
  const qc = useQueryClient()

  // Live network meta — used to guard autosave against saving an empty network
  const { data: networkMeta } = useQuery({
    queryKey: nk(currentProject, 'meta'),
    queryFn: networkApi.getMeta,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const networkHasBuses = (networkMeta?.bus_count ?? 0) > 0

  const guardProjectMutation = useCallback((opts?: { silent?: boolean }) => {
    const verdict = evaluateMutation(readOnly, readOnlyReason)
    if (verdict.allowed) return true
    if (opts?.silent) appLog('INFO', `Autosave skipped — ${verdict.blockedMessage}`)
    else toast.error(verdict.blockedMessage!)
    return false
  }, [readOnly, readOnlyReason])

  // Save current network state to backend (under `name`), then offer the
  // resulting bundle as a download via the OS save-file picker (Chromium) or
  // the browser's default download mechanism (Firefox/Safari).
  // The backend save also persists user_ts.json so autosave + reload paths stay
  // consistent.
  const saveAndExportBundle = useCallback(async (
    name: string,
    opts?: { auto?: boolean; setAsCurrent?: boolean; askLocation?: boolean; skipCache?: boolean },
  ) => {
    const auto = !!opts?.auto
    const setAsCurrent = opts?.setAsCurrent !== false
    const activeProject = useUIStore.getState().currentProject
    const savingActiveProject = activeProject !== null && name === activeProject && setAsCurrent
    if ((auto || savingActiveProject) && !guardProjectMutation({ silent: auto })) return
    try {
      // Identity assertion: when this save targets the ACTIVE project (autosave
      // or explicit Ctrl+S — name === currentProject), pass `expect` so the
      // backend refuses (409) if its in-memory network was swapped to a
      // different project. Saving under a DIFFERENT name on purpose (Save a
      // Copy, or the first save of a brand-new project where currentProject is
      // still null) omits `expect` so the write is allowed. Read currentProject
      // fresh — a switch may have completed since this callback was created.
      // Commit any line/link delete still inside its 5 s undo window BEFORE
      // serialising. Until it fires, the backend network still holds the edge
      // the canvas already removed — saving first writes a network.nc that
      // disagrees with what the user is looking at, and reopening the project
      // brings the deleted line back. Save means "persist what I see".
      const drained = await flushPendingEdgeDeletes()
      if (drained.flushed || drained.failed) {
        appLog(drained.failed ? 'WARN' : 'INFO',
          `Committed ${drained.flushed} pending edge delete(s) before saving '${name}'` +
          (drained.failed ? ` · ${drained.failed} failed` : ''))
      }
      const cur = useUIStore.getState().currentProject
      const expect = name === cur ? name : undefined
      // Rebind when this save makes `name` the active project (setAsCurrent).
      // First-save of a new project and Save (current) both rebind/claim; Save
      // a Copy passes setAsCurrent:false so the binding stays on the original.
      const rebind = setAsCurrent
      // Explicit user saves drop the undo stack (act as a checkpoint).
      // Autosave preserves it so the user can still revert recent edits.
      const result = await projectsApi.save(name, false, !auto, expect, rebind)
      // Flush the diagram layout (node positions + edge waypoints) to
      // layout.json AFTER the project save creates the directory. Without
      // this, the Sidebar Save path persists network.nc but never updates
      // layout.json — a freshly dragged bus position would be lost on the
      // next reload because the canvas reads from the stale on-disk
      // layout. Mirrors the AppHeader save flow.
      let layoutFlushResult: 'nothing' | 'server' | 'local' | 'error' = 'nothing'
      let layoutNodes = 0
      let layoutEdges = 0
      try {
        const { flushPendingLayoutToServer } = await import('../pages/TopologyCanvas')
        const r = await flushPendingLayoutToServer(result.saved)
        layoutFlushResult = r.status
        layoutNodes = r.nodes
        layoutEdges = r.edges
        if (r.status === 'server') {
          appLog('INFO', `Layout saved · ${r.nodes} node(s) + ${r.edges} edge(s) → layout.json`)
        } else if (r.status === 'local') {
          appLog('WARN', `Layout server write failed — kept ${r.nodes} node(s) + ${r.edges} edge(s) in localStorage`)
        }
      } catch (e) {
        layoutFlushResult = 'error'
        appLog('WARN', `Layout flush failed for '${result.saved}': ${String((e as Error)?.message ?? e)}`)
      }
      if (setAsCurrent) setCurrentProject(name)
      markProjectSaved(name)
      const tsCols = result.ts_columns_saved ?? 0
      appLog('INFO', `${auto ? 'Autosave' : 'Save'} '${name}': ${result.bus_count} buses, ${tsCols} ts columns`)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
      // The depth counter polls every 3s; force-refresh it so the badge clears
      // immediately after an explicit Save.
      if (!auto) qc.invalidateQueries({ queryKey: nk(name, 'undoInfo') })

      if (auto) return  // autosave does not download a bundle

      const mode = await downloadProjectBundle(name, {
        askLocation: opts?.askLocation,
        skipCache: opts?.skipCache,
      })
      // Compose a "+ layout" hint when the layout-flush actually wrote
      // positions, so the user has visible confirmation that the diagram
      // was persisted alongside the network. Quiet on 'nothing' (no drag
      // = nothing to write); warning on 'local' / 'error' (server write
      // failed — appLog already explained why).
      const layoutHint =
        layoutFlushResult === 'server'
          ? ` · layout: ${layoutNodes}n/${layoutEdges}e`
          : layoutFlushResult === 'local'
            ? ` · ⚠ layout to localStorage only`
            : layoutFlushResult === 'error'
              ? ` · ⚠ layout flush failed`
              : ''
      if (mode === 'cancelled') {
        // User cancelled the save dialog; backend save still succeeded.
        toast(`Saved on server · ${name}${layoutHint}`, { icon: '💾' })
      } else {
        const suffix = mode === 'download' ? ' — downloaded'
          : mode === 'reused' ? ' — updated saved file'
          : '' // 'picker' — first save / location chosen
        toast.success(
          `Saved · ${name} (${tsCols} ts col${tsCols !== 1 ? 's' : ''})${suffix}${layoutHint}`,
        )
      }
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        const detail = String(
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '',
        )
        // Two distinct 409s from save_project: (a) identity mismatch — the
        // backend's in-memory network is a DIFFERENT project than `name` (it
        // was swapped by another tab / external client); (b) empty-network
        // refusal. Both mean "don't save", but the user-facing guidance
        // differs. Detail text disambiguates.
        if (/bound to project/i.test(detail)) {
          if (auto) appLog('WARN', `Autosave skipped — ${detail}`)
          else toast.error(`Can't save '${name}': the backend is on a different project. Reload '${name}' to resync.`)
        } else {
          if (auto) appLog('WARN', `Autosave skipped: network is empty, refusing to overwrite '${name}'`)
          else toast.error('Cannot save: network is empty but project has data. Load the project first.')
        }
        return
      }
      if (!auto) toast.error('Save failed')
      appLog('ERROR', `Save failed: ${String((e as Error)?.message ?? e)}`)
    }
  }, [setCurrentProject, setSaveStatus, qc, markProjectSaved, guardProjectMutation])

  // Autosave: every 5 minutes when enabled, a project is set, and the
  // in-memory network has at least one bus (guards against server restarts).
  // Autosave never opens a download dialog.
  //
  // Skipped while a simulation is running. During a multi-hour solve the
  // network carries transient LP transforms (vintage rows, slack
  // generators, dispatch fix); the backend returns 409 on every attempt
  // and the user sees a noisy "Cannot save while solver is active" line
  // every 5 minutes for hours. The pre-run snapshot taken by the Run-LOPF
  // button covers the "save the last known good state" use case — the
  // periodic save can safely pause for the duration of the solve and
  // resume once the worker exits.
  useEffect(() => {
    if (!autosaveEnabled || !currentProject) return
    const id = setInterval(async () => {
      // Read sim status fresh on every tick — keeping it out of the deps
      // array means the interval isn't torn down + recreated on every
      // status flip (idle → running → completed). Cheap snapshot read.
      const simStatus = useSimulationStore.getState().status
      if (simStatus === 'running') {
        appLog('INFO', 'Autosave paused — simulation running. Will resume after solve completes.')
        return
      }
      // Skip if a project switch/load is mid-flight. During the window between
      // the backend swapping the in-memory network and `currentProject` being
      // updated, this closure's `currentProject` (and even a fresh read) can
      // disagree with the network actually in memory — autosaving then would
      // serialise the newly-loaded network under the OLD project's folder, a
      // silent cross-project overwrite. The flag is set/cleared around the
      // switch sequence, so we just bail this tick and catch the next one.
      if (useUIStore.getState().projectSwitchInProgress) {
        appLog('INFO', 'Autosave skipped — project switch in progress.')
        return
      }
      // Save under the CURRENT project read fresh from the store (not the
      // closure), so a switch that completed since this interval was created
      // can't autosave under a stale name.
      const cur = useUIStore.getState().currentProject
      if (!cur) return
      if (!networkHasBuses) {
        appLog('WARN', 'Autosave skipped: network is empty — load project or add components first')
        return
      }
      // Identity is enforced atomically server-side now: saveAndExportBundle
      // passes expect=cur, and save_project refuses (409) under the PyPSA lock
      // if its in-memory network is bound to a DIFFERENT project — the
      // cross-project overwrite that destroyed a project on 2026-05-28. This
      // replaces the old client-side getMeta probe, which had a TOCTOU gap (the
      // network could swap between the probe and the POST) and only protected
      // this one caller. The 409 is handled in saveAndExportBundle's catch.
      saveAndExportBundle(cur, { auto: true })
    }, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [autosaveEnabled, currentProject, networkHasBuses, saveAndExportBundle])

  const handleSave = () => {
    if (currentProject) saveAndExportBundle(currentProject)
    else setShowNameModal(true)
  }

  const handleNameConfirm = (name: string) => {
    setShowNameModal(false)
    saveAndExportBundle(name)
  }

  const handleSaveCopy = () => {
    // Always prompt — a "copy" needs a name distinct from the current project.
    // The modal pre-fills "<current>_copy" as a starting point.
    setShowCopyModal(true)
  }

  const handleCopyConfirm = (name: string) => {
    setShowCopyModal(false)
    // setAsCurrent: false → the active project tab keeps pointing at the
    // original.
    // askLocation: true → always show the picker so the copy can land
    // wherever the user wants, regardless of any cached handle.
    // skipCache: true → don't update the cache, so the next regular Save of
    // the original project keeps writing to its original location.
    saveAndExportBundle(name, { setAsCurrent: false, askLocation: true, skipCache: true })
  }

  const handleOpenPick = async (name: string) => {
    setShowOpenModal(false)
    const tId = toast.loading(`Opening '${name}'…`)
    setProjectSwitchInProgress(true)
    try {
      // Re-load when name === currentProject: covers the "stale frontend, empty
      // backend" case after a server/browser reload — the tab still says "X"
      // but PyPSA's in-memory network is empty, so force a disk re-read. This
      // is a DESTRUCTIVE re-load (not the instant switch) — switchToProject
      // no-ops on the same project, so it can't serve this recovery path.
      if (name === currentProject) {
        const stopped = await abortRunningSim()
        if (!stopped) {
          toast.error('Could not abort the running simulation. Try again in a moment.', { id: tId })
          return
        }
        await projectsApi.load(name)
        invalidateNetworkQueries(qc, name)
        setProjectName(name)
        appLog('INFO', `Reloaded project '${name}'`)
        toast.success(`Opened '${name}'`, { id: tId })
        return
      }
      // Different project → B8 instant switch (activate + re-key to the
      // target's RETAINED cache, no component-table refetch). setCurrentProject
      // (inside switchToProject) auto-adds the tab if not already open.
      const r = await switchToProject(name, qc)
      switch (r.status) {
        case 'switched':
          appLog('INFO', `Opened project '${name}'`)
          toast.success(`Opened '${name}'`, { id: tId })
          break
        case 'noop':
          toast.dismiss(tId)
          break
        case 'abort-failed':
          toast.error('Could not abort the running simulation. Try again in a moment.', { id: tId })
          break
        case 'busy-solve':
          toast.error(`Finish or abort the running solve on '${currentProject}' first.`, { id: tId })
          break
        case 'not-found':
          toast.error(`'${name}' no longer exists`, { id: tId })
          break
        case 'error':
          appLog('ERROR', `Open '${name}' failed: ${r.message}`)
          toast.error(`Could not open '${name}'`, { id: tId })
          break
      }
    } catch (e) {
      // Same seam as the clone wizard: `name === currentProject` above calls
      // `projectsApi.load`, which is `load_project` — the route that now
      // refuses (409, error_kind `solver_in_flight`) while a queue job owns
      // this project's context. A bare "Could not open" here would hide
      // exactly the reason this whole fix exists to surface.
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const msg = formatApiDetail(detail, (e as Error)?.message ?? String(e))
      appLog('ERROR', `Open '${name}' failed: ${msg}`)
      toast.error(`Could not open '${name}': ${msg}`, { id: tId })
    } finally {
      setProjectSwitchInProgress(false)
    }
  }

  const handleOpenFromFile = async (file: File) => {
    setShowOpenModal(false)
    try {
      const stopped = await abortRunningSim()
      if (!stopped) {
        toast.error('Could not abort the running simulation. Try again in a moment.')
        return
      }
      if (currentProject) await saveProjectQuietly(currentProject)
      // No `targetName` → backend uses the bundle's metadata.json name (or
      // the filename) and registers it as a fresh project. Importing replaces
      // the in-memory network, so the prior auto-save above is essential.
      const res = await projectsApi.importBundle(file)
      invalidateNetworkQueries(qc, res.imported)
      qc.invalidateQueries({ queryKey: nk(res.imported, 'results') })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(res.imported)
      setProjectName(res.imported)
      appLog('INFO', `Opened project '${res.imported}' from disk (${file.name})`)
      toast.success(`Opened '${res.imported}' (network + results when present)`)
    } catch (e) {
      appLog('ERROR', `Open from disk failed: ${String((e as Error)?.message ?? e)}`)
      toast.error('Could not open project from file')
    }
  }

  // Dirty state: any pending undo step means there are unsaved edits since
  // the last explicit Save (which clears the undo stack as its checkpoint).
  // Re-polls every 3 s — matches the cadence in AppHeader so badges stay in sync.
  const { data: undoInfo } = useQuery({
    queryKey: nk(currentProject, 'undoInfo'),
    queryFn: networkApi.undoInfo,
    refetchInterval: 3000,
    staleTime: 0,
  })
  const dirty = (undoInfo?.depth ?? 0) > 0

  const lastSavedIso = currentProject ? lastSavedByProject[currentProject] ?? null : null
  // Recents minus the active project — that's already shown in the header card.
  const recentsToShow = recents.filter(r => r !== currentProject).slice(0, 5)

  return (
    <div>
      {showNameModal && (
        <SaveNameModal
          initial={slugify(currentProject || pn)}
          onSave={handleNameConfirm}
          onClose={() => setShowNameModal(false)}
        />
      )}
      {showCopyModal && (
        <SaveNameModal
          initial={slugify(`${currentProject || pn}_copy`)}
          onSave={handleCopyConfirm}
          onClose={() => setShowCopyModal(false)}
        />
      )}
      {showOpenModal && (
        <OpenProjectModal
          currentProject={currentProject}
          onPick={handleOpenPick}
          onPickFile={handleOpenFromFile}
          onClose={() => setShowOpenModal(false)}
        />
      )}

      <ProjectHeaderCard
        currentProject={currentProject}
        lastSavedIso={lastSavedIso}
        dirty={dirty}
        autosaveEnabled={autosaveEnabled}
        onToggleAutosave={() => setAutosaveEnabled(!autosaveEnabled)}
        // Rename: focuses the header's project-name button. The button's
        // onFocus handler (`startEditName`) flips to edit mode and schedules
        // `nameRef.current?.focus().select()` on the new <input>, so a single
        // .focus() here is enough — no DOM-selection juggling needed.
        onRename={() => {
          const el = document.querySelector<HTMLElement>('[data-project-name-editor]')
          el?.focus()
        }}
      />

      {currentProject && (
        <>
          <SubHdr title="Quick actions" />
          <SItem
            icon={<LayoutDashboard size={15} />}
            label="Project info"
            active={activeSlidePanel === 'overview'}
            onClick={() => {
              setSlidePanel(activeSlidePanel === 'overview' ? null : 'overview')
              onCloseModal?.()
            }}
          />
          <SItem
            icon={saveStatus === 'saved' ? <Check size={15} /> : <Save size={15} />}
            label={saveStatus === 'saved' ? 'Saved ✓' : 'Save'}
            hint={dirty ? '●' : undefined}
            onClick={handleSave}
          />
          <SItem
            icon={<Camera size={15} />}
            label="Snapshots"
            active={activeSlidePanel === 'snapshots'}
            onClick={() => {
              // Toggle: if the snapshots panel is already open, clicking the
              // sidebar entry again closes it (consistent with TimeSeries /
              // Results behaviour).
              setSlidePanel(activeSlidePanel === 'snapshots' ? null : 'snapshots')
              onCloseModal?.()
            }}
          />
          <SItem
            icon={<GitBranchIcon size={15} />}
            label="Scenarios"
            active={activeSlidePanel === 'scenarios'}
            onClick={() => {
              setSlidePanel(activeSlidePanel === 'scenarios' ? null : 'scenarios')
              onCloseModal?.()
            }}
          />
          <SItem
            icon={<Copy size={15} />}
            label="Duplicate project"
            onClick={handleSaveCopy}
          />
          <SItem
            icon={<FileDown size={15} />}
            label="Export bundle"
            onClick={() => { openIo('export'); onCloseModal?.() }}
          />
        </>
      )}

      {recentsToShow.length > 0 && (
        <>
          <SubHdr title="Recent" />
          {recentsToShow.map(name => {
            const stamp = lastSavedByProject[name]
            const relative = formatRelativeTime(stamp)
            return (
              <SItem
                key={name}
                icon={<Clock size={15} />}
                label={name}
                hint={relative ?? undefined}
                onClick={() => { handleOpenPick(name); onCloseModal?.() }}
              />
            )
          })}
        </>
      )}

      {/* The old "Workspace" block (New project / Open from disk / Import /
          From template) now lives on the projects home — creating a project
          belongs to the workspace, not to whichever network is resident here.
          All four are the tabs of the same NewProjectWizard, which that page
          opens directly. What stays is the way BACK to the workspace. */}
      <SubHdr title="Workspace" />
      <SItem
        icon={<LayoutGrid size={15} />}
        label="Projects home"
        title="Browse every project you can access, resume the last one, or create / import a new one."
        onClick={() => { onCloseModal?.(); navigate('/projects') }}
      />
      <SItem
        icon={<Users size={15} />}
        label="Workspace panel"
        title="Ownership, edit lock, scenario tree, members, and recent projects for the open project."
        active={activeSlidePanel === 'workspace'}
        onClick={() => {
          setSlidePanel(activeSlidePanel === 'workspace' ? null : 'workspace')
          onCloseModal?.()
        }}
      />
    </div>
  )
}

function DataSectionContent({ onCloseModal }: { onCloseModal?: () => void }) {
  const [assetsOpen, setAssetsOpen] = useState(false)
  const { setSlidePanel, activeSlidePanel } = useUIStore()
  return (
    <div>
      <SItem
        icon={<Layers size={15} />}
        label="Assets"
        rightEl={
          <ChevronDown size={11} className="text-ink-400 shrink-0 transition-transform duration-200"
            style={{ transform: assetsOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
        }
        onClick={() => setAssetsOpen(o => !o)}
      />
      {assetsOpen && <AssetPaletteInline />}
      <SItem icon={<BarChart2 size={15} />} label="Time Series"
        active={activeSlidePanel === 'timeseries'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'timeseries' ? null : 'timeseries'); onCloseModal?.() }}
      />
    </div>
  )
}

function SimulationSectionContent({ onCloseModal, requestBottomTab }: {
  onCloseModal?: () => void
  requestBottomTab: (tab: string) => void
}) {
  const { setSlidePanel, activeSlidePanel, currentProject } = useUIStore()
  void requestBottomTab
  // Polls /preflight to surface the error+warning count as a badge on the
  // Issues row. 30 s feels right for an out-of-view indicator — long enough
  // not to thrash the backend, short enough that the badge stays roughly
  // accurate after an edit. The panel itself polls more aggressively when
  // open.
  const { data: preflight } = useQuery({
    queryKey: nk(currentProject, 'preflight'),
    queryFn: simulationApi.preflight,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const issueCount = (preflight?.errors ?? 0) + (preflight?.warnings ?? 0)
  const hasErrors = (preflight?.errors ?? 0) > 0
  // Active (queued + running) solve-queue jobs, surfaced as a badge on the
  // Solve queue row. Shares the ['solveQueue'] query — polls only while jobs
  // are active (see useSolveQueue), so this idle-mounted consumer is cheap.
  const { data: solveQueue } = useSolveQueue()
  const activeQueueCount = (solveQueue?.jobs ?? []).filter(isActive).length
  // The Settings pane is desktop-only; its routes 404 on a web deployment.
  // The row hides with it — a nav entry that opens an empty panel is worse
  // than no nav entry. Shares one react-query fetch with the pane.
  const settingsAvailable = useLocalSettingsAvailable()
  return (
    <div>
      <SItem icon={<Settings2 size={15} />} label="Solver Settings"
        title="Choose the solver, foresight mode (overnight / myopic / perfect), CO₂ price, discount rate and value-of-lost-load."
        active={activeSlidePanel === 'simparams'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'simparams' ? null : 'simparams'); onCloseModal?.() }}
      />
      {settingsAvailable && (
        <SItem icon={<SlidersHorizontal size={15} />} label="Settings"
          title="Store your Anthropic API key and find the application log."
          active={activeSlidePanel === 'settings'}
          onClick={() => { setSlidePanel(activeSlidePanel === 'settings' ? null : 'settings'); onCloseModal?.() }}
        />
      )}
      <SItem icon={<Clock size={15} />} label="Model Horizon"
        title="Define the snapshots (time steps) and investment periods/years the optimisation plans over."
        active={activeSlidePanel === 'horizon'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'horizon' ? null : 'horizon'); onCloseModal?.() }}
      />
      <SItem icon={<Layers size={15} />} label="Capacity Bounds"
        title="Set per-technology minimum/maximum build limits, optionally different for each investment period (vintages)."
        active={activeSlidePanel === 'capacityBounds'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'capacityBounds' ? null : 'capacityBounds'); onCloseModal?.() }}
      />
      <SItem
        icon={<AlertTriangle size={15} />}
        label="Issues"
        active={activeSlidePanel === 'issues'}
        rightEl={issueCount > 0 ? (
          <span
            className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{
              background: hasErrors ? 'rgba(239,68,68,0.18)' : 'rgba(217,119,6,0.18)',
              color:      hasErrors ? '#f87171'             : '#f59e0b',
            }}
          >{issueCount}</span>
        ) : undefined}
        onClick={() => { setSlidePanel(activeSlidePanel === 'issues' ? null : 'issues'); onCloseModal?.() }}
      />
      <SItem icon={<TrendingUp size={15} />} label="Results"
        active={activeSlidePanel === 'results'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'results' ? null : 'results'); onCloseModal?.() }}
      />
      <SItem icon={<ListChecks size={15} />} label="Solve Queue"
        title="Queue saved projects to solve sequentially in the background; results persist to disk."
        active={activeSlidePanel === 'solveQueue'}
        rightEl={activeQueueCount > 0 ? (
          <span
            className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{ background: 'rgba(217,119,6,0.18)', color: '#f59e0b' }}
          >{activeQueueCount}</span>
        ) : undefined}
        onClick={() => { setSlidePanel(activeSlidePanel === 'solveQueue' ? null : 'solveQueue'); onCloseModal?.() }}
      />
      {/* Chatbot integration v6 (Phase 3) — toggle the ChatPanel slide. */}
      <SItem icon={<MessageSquare size={15} />} label="Chat"
        title="Conversational assistant. Ask questions about the open network, drive tools, confirm destructive actions through a card."
        active={activeSlidePanel === 'chat'}
        onClick={() => { setSlidePanel(activeSlidePanel === 'chat' ? null : 'chat'); onCloseModal?.() }}
      />
    </div>
  )
}

// ── Mode buttons ───────────────────────────────────────────────────────────────
function ModeSwitcher({ small = false }: { small?: boolean }) {
  const { canvasMode, setCanvasMode } = useUIStore()
  const ConnectIcon = () => (
    <svg width={small ? 12 : 14} height={small ? 12 : 14} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <circle cx="3" cy="8" r="2.5" /><circle cx="13" cy="8" r="2.5" />
      <line x1="5.5" y1="8" x2="10.5" y2="8" />
    </svg>
  )
  if (small) {
    return (
      <div className="flex flex-col gap-1 items-center py-2">
        {([
          { id: 'select', Icon: MousePointer, hint: 'V' },
          { id: 'connect', Icon: ConnectIcon, hint: 'C' },
        ] as const).map(({ id, Icon, hint }) => (
          <button
            key={id}
            onClick={() => setCanvasMode(id)}
            title={id === 'select' ? `Select / Pan  ${hint}` : `Connect Buses  ${hint}`}
            className="relative flex items-center justify-center rounded transition-colors"
            style={{
              width: 32, height: 32,
              background: canvasMode === id ? 'rgba(var(--color-accent-rgb, 47,129,247),0.2)' : undefined,
              color: canvasMode === id ? 'var(--color-accent)' : 'var(--color-muted)',
              border: canvasMode === id ? '1px solid rgba(47,129,247,0.5)' : '1px solid transparent',
            }}
          >
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <Icon size={13} {...({} as any)} />
          </button>
        ))}
        <button title="Zoom in"
          className="flex items-center justify-center rounded transition-colors" style={{ width: 32, height: 28, color: 'var(--color-ink-400)', border: '1px solid transparent' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-ink-400)' }}
          onClick={() => (window as unknown as { rfInstance?: { zoomIn: () => void } }).rfInstance?.zoomIn?.()}>
          <ZoomIn size={12} />
        </button>
        <button title="Zoom out"
          className="flex items-center justify-center rounded transition-colors" style={{ width: 32, height: 28, color: 'var(--color-ink-400)', border: '1px solid transparent' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-ink-400)' }}
          onClick={() => (window as unknown as { rfInstance?: { zoomOut: () => void } }).rfInstance?.zoomOut?.()}>
          <ZoomOut size={12} />
        </button>
      </div>
    )
  }

  return (
    <div className="shrink-0 border-t border-border p-2">
      <p className="px-2 pb-1 text-[9px] font-bold uppercase tracking-widest text-ink-400">MODE</p>
      {([
        { id: 'select',  label: 'Select / Pan',  Icon: MousePointer, hint: 'V' },
        { id: 'connect', label: 'Connect Buses', Icon: ConnectIcon,  hint: 'C' },
      ] as const).map(({ id, label, Icon, hint }) => (
        <button key={id} onClick={() => setCanvasMode(id)}
          className="flex items-center gap-2 w-full px-2 py-1.5 rounded text-[11px] mb-0.5 transition-colors"
          style={{
            background: canvasMode === id ? 'rgba(47,129,247,0.15)' : undefined,
            color: canvasMode === id ? 'var(--color-accent)' : 'var(--color-muted)',
          }}
          onMouseEnter={e => { if (canvasMode !== id) (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' }}
          onMouseLeave={e => { if (canvasMode !== id) (e.currentTarget as HTMLElement).style.background = '' }}
        >
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <Icon size={13} {...({} as any)} />
          <span className="flex-1">{label}</span>
          <span className="text-[9px] font-mono text-ink-300">{hint}</span>
        </button>
      ))}
      <div className="border-t border-border mt-2 pt-2 flex gap-1">
        <button className="flex-1 flex items-center justify-center gap-1 py-1 text-[10px] rounded transition-colors"
          style={{ color: 'var(--color-muted)', border: '1px solid var(--color-border)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)' }}
          onClick={() => (window as unknown as { rfInstance?: { zoomIn: () => void } }).rfInstance?.zoomIn?.()}>
          <ZoomIn size={11} /> In
        </button>
        <button className="flex-1 flex items-center justify-center gap-1 py-1 text-[10px] rounded transition-colors"
          style={{ color: 'var(--color-muted)', border: '1px solid var(--color-border)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)' }}
          onClick={() => (window as unknown as { rfInstance?: { zoomOut: () => void } }).rfInstance?.zoomOut?.()}>
          <ZoomOut size={11} /> Out
        </button>
      </div>
    </div>
  )
}

// Theme + density toggles. Sits below ModeSwitcher in the sidebar footer.
// Buttons show the CURRENT state's icon (Sun=light, Moon=dark; Rows2=comfortable,
// Rows3=compact); click toggles to the other. The same controls are mirrored in
// the command palette so keyboard users don't need to mouse-over the sidebar.
function PreferencesFooter({ small = false }: { small?: boolean }) {
  // Per-field selectors instead of bare `useUIStore()` — otherwise this small
  // footer block re-renders on every unrelated store change (canvasMode,
  // resultsSnapshotIdx, …). Matches the pattern documented in CommandPalette.tsx.
  const theme         = useUIStore(s => s.theme)
  const density       = useUIStore(s => s.density)
  const toggleTheme   = useUIStore(s => s.toggleTheme)
  const toggleDensity = useUIStore(s => s.toggleDensity)
  const themeTitle = theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'
  const densityTitle = density === 'comfortable' ? 'Switch to compact density' : 'Switch to comfortable density'
  const ThemeIcon = theme === 'light' ? Sun : Moon
  const DensityIcon = density === 'comfortable' ? Rows2 : Rows3

  if (small) {
    return (
      <div className="flex flex-col gap-1 items-center py-2 border-t border-border">
        <button
          onClick={toggleTheme} title={themeTitle}
          className="flex items-center justify-center rounded transition-colors"
          style={{
            width: 32, height: 28,
            color: 'var(--color-muted)',
            border: '1px solid transparent',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)' }}
        >
          <ThemeIcon size={13} />
        </button>
        <button
          onClick={toggleDensity} title={densityTitle}
          className="flex items-center justify-center rounded transition-colors"
          style={{
            width: 32, height: 28,
            color: 'var(--color-muted)',
            border: '1px solid transparent',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)' }}
        >
          <DensityIcon size={13} />
        </button>
      </div>
    )
  }

  return (
    <div className="shrink-0 border-t border-border p-2">
      <p className="px-2 pb-1 text-[9px] font-bold uppercase tracking-widest text-ink-400">PREFERENCES</p>
      <div className="flex gap-1">
        <button
          onClick={toggleTheme} title={themeTitle}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] rounded transition-colors"
          style={{ color: 'var(--color-muted)', border: '1px solid var(--color-border)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '' }}
        >
          <ThemeIcon size={12} />
          <span className="capitalize">{theme}</span>
        </button>
        <button
          onClick={toggleDensity} title={densityTitle}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] rounded transition-colors"
          style={{ color: 'var(--color-muted)', border: '1px solid var(--color-border)' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '' }}
        >
          <DensityIcon size={12} />
          <span className="capitalize">{density === 'comfortable' ? 'Comfy' : 'Compact'}</span>
        </button>
      </div>
    </div>
  )
}

// ── Icon strip section button ──────────────────────────────────────────────────
type FlyoutSection = 'project' | 'data' | 'simulation'

function IconStripBtn({
  icon, label, sectionId, activeFlyout, onClick,
}: {
  icon: React.ReactNode; label: string
  sectionId: FlyoutSection; activeFlyout: FlyoutSection | null
  onClick: () => void
}) {
  const active = activeFlyout === sectionId
  return (
    <button
      onClick={onClick}
      title={label}
      className="relative flex items-center justify-center transition-colors"
      style={{
        width: SIDEBAR_ICON_W, height: 44,
        color: active ? 'var(--color-accent)' : 'var(--color-muted)',
        background: active ? 'rgba(47,129,247,0.12)' : undefined,
        borderLeft: active ? '3px solid var(--color-accent)' : '3px solid transparent',
      }}
      onMouseEnter={e => { if (!active) { (e.currentTarget as HTMLElement).style.color = 'var(--color-text)'; (e.currentTarget as HTMLElement).style.background = 'var(--color-border)' } }}
      onMouseLeave={e => { if (!active) { (e.currentTarget as HTMLElement).style.color = 'var(--color-muted)'; (e.currentTarget as HTMLElement).style.background = '' } }}
    >
      {icon}
    </button>
  )
}

// ── Flyout panel ───────────────────────────────────────────────────────────────
function FlyoutPanel({
  section, onClose, projectName, saveStatus, setSaveStatus, openIo, handleNewProject, requestBottomTab,
}: {
  section: FlyoutSection; onClose: () => void; projectName: string
  saveStatus: 'idle' | 'saved'; setSaveStatus: (s: 'idle' | 'saved') => void
  openIo: (tab: 'import' | 'export') => void; handleNewProject: () => void
  requestBottomTab: (tab: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  const SECTION_LABELS: Record<FlyoutSection, string> = {
    project: 'PROJECT', data: 'DATA', simulation: 'SIMULATION',
  }

  // Close on outside click
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [onClose])

  return (
    <div
      ref={ref}
      className="fixed z-[800] flex flex-col overflow-hidden"
      style={{
        left: SIDEBAR_ICON_W, top: 44,
        width: SIDEBAR_EXPANDED_W,
        maxHeight: 'calc(100vh - 44px)',
        background: 'var(--color-nav)',
        borderRight: '1px solid var(--color-border)',
        borderBottom: '1px solid var(--color-border)',
        boxShadow: '4px 4px 24px rgba(0,0,0,0.30)',
      }}
    >
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
        <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-ink-400">
          {SECTION_LABELS[section]}
        </span>
        <button onClick={onClose} className="text-ink-400 hover:text-text transition-colors">
          <X size={12} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {section === 'project' && (
          <ProjectSectionContent
            onCloseModal={onClose}
            projectName={projectName}
            saveStatus={saveStatus} setSaveStatus={setSaveStatus}
            openIo={openIo} handleNewProject={handleNewProject}
          />
        )}
        {section === 'data' && <DataSectionContent onCloseModal={onClose} />}
        {section === 'simulation' && (
          <SimulationSectionContent onCloseModal={onClose} requestBottomTab={requestBottomTab} />
        )}
      </div>
    </div>
  )
}

// ── Main Sidebar ───────────────────────────────────────────────────────────────
export default function Sidebar() {
  const {
    sidebarMode, setSidebarMode, toggleSidebar, canvasMode, setCanvasMode,
    activeSlidePanel, projectName, requestBottomTab,
    setCurrentProject, setProjectName, currentProject, addTab,
    ioModalRequest, clearIoModalRequest,
  } = useUIStore()
  const [sections, setSections] = useState({ project: true, data: true, simulation: true })
  const [activeFlyout, setActiveFlyout] = useState<FlyoutSection | null>(null)
  const [ioOpen, setIoOpen] = useState(false)
  const [ioTab, setIoTab] = useState<'import' | 'export'>('import')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved'>('idle')
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const qc = useQueryClient()

  // Chat / agent: open Import/Export modal on request.
  useEffect(() => {
    if (!ioModalRequest) return
    setIoTab(ioModalRequest)
    setIoOpen(true)
    clearIoModalRequest()
  }, [ioModalRequest, clearIoModalRequest])

  const { data: allProjects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 30_000,
  })

  const newProjectMut = useMutation({
    mutationFn: async (name: string) => {
      // Auto-save the active project so it isn't lost when the new tab
      // takes over the single backend network slot.
      if (currentProject && currentProject !== name) {
        await saveProjectQuietly(currentProject)
      }
      await resetBackendNetwork()
      await projectsApi.save(name, true) // force=true: user explicitly chose to create/overwrite
      return name
    },
    onSuccess: (name: string) => {
      invalidateNetworkQueries(qc, name)
      addTab(name)
      setCurrentProject(name)
      setProjectName(name)
      setNewProjectOpen(false)
      toast.success(`Project "${name}" created`)
      appLog('INFO', `New project created: ${name}`)
    },
    onError: (e: Error) => {
      toast.error(`Failed to create project: ${e.message}`)
      appLog('ERROR', `New project failed: ${e.message}`)
    },
  })

  const handleNewProject = useCallback(() => {
    setNewProjectOpen(true)
  }, [])

  const openIo = useCallback((tab: 'import' | 'export') => {
    setIoTab(tab); setIoOpen(true)
  }, [])

  const toggleSection = (k: keyof typeof sections) => setSections(s => ({ ...s, [k]: !s[k] }))

  const toggleFlyout = useCallback((section: FlyoutSection) => {
    setActiveFlyout(prev => prev === section ? null : section)
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return
      if (e.key === '[' && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); toggleSidebar() }
      if (e.ctrlKey && e.key === '\\') { e.preventDefault(); toggleSidebar() }
      if (!e.ctrlKey && !e.altKey && !e.metaKey) {
        if (e.key === 'v' || e.key === 'V' || e.key === 'Escape') setCanvasMode('select')
        if (e.key === 'c' || e.key === 'C') setCanvasMode('connect')
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [toggleSidebar, setCanvasMode])

  // Close flyout on Escape
  useEffect(() => {
    if (!activeFlyout) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setActiveFlyout(null) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [activeFlyout])

  // ── Hidden → floating tab ──────────────────────────────────────────────────
  if (sidebarMode === 'hidden') {
    return (
      <button
        onClick={() => setSidebarMode('icon')}
        title="Show sidebar  ["
        className="flex items-center justify-center transition-colors"
        style={{
          position: 'fixed', left: 0, top: '50%', transform: 'translateY(-50%)',
          zIndex: 200, width: 20, height: 48,
          background: 'var(--color-nav)', borderRadius: '0 6px 6px 0',
          border: '1px solid var(--color-border)', borderLeft: 'none', color: 'var(--color-muted)',
        }}
      >
        <ChevronRight size={12} />
      </button>
    )
  }

  // ── Icon strip (48px) ──────────────────────────────────────────────────────
  if (sidebarMode === 'icon') {
    return (
      <>
        <aside
          className="shrink-0 bg-nav border-r border-border h-full flex flex-col items-center overflow-hidden"
          style={{ width: SIDEBAR_ICON_W, minWidth: SIDEBAR_ICON_W }}
        >
          {/* Brand icon + expand */}
          <div className="flex flex-col items-center w-full border-b border-border shrink-0 py-2 gap-0.5"
            style={{ height: 44, justifyContent: 'center' }}>
            <button
              onClick={() => setSidebarMode('expanded')}
              title="Expand sidebar"
              className="flex items-center justify-center text-muted hover:text-text transition-colors"
              style={{ width: 32, height: 32 }}
            >
              <span className="text-[16px] leading-none">⚡</span>
            </button>
          </div>

          {/* Section icons */}
          <div className="flex-1 flex flex-col w-full">
            <IconStripBtn icon={<FolderOpen size={18} />}  label="Project"    sectionId="project"    activeFlyout={activeFlyout} onClick={() => toggleFlyout('project')} />
            <IconStripBtn icon={<Layers size={18} />}       label="Data"       sectionId="data"       activeFlyout={activeFlyout} onClick={() => toggleFlyout('data')} />
            <IconStripBtn icon={<Settings2 size={18} />}   label="Simulation" sectionId="simulation" activeFlyout={activeFlyout} onClick={() => toggleFlyout('simulation')} />
          </div>

          {/* Mode switcher */}
          <div className="shrink-0 border-t border-border w-full">
            <ModeSwitcher small />
            <PreferencesFooter small />
          </div>
        </aside>

        {activeFlyout && (
          <FlyoutPanel
            section={activeFlyout}
            onClose={() => setActiveFlyout(null)}
            projectName={projectName}
            saveStatus={saveStatus} setSaveStatus={setSaveStatus}
            openIo={openIo}
            handleNewProject={handleNewProject}
            requestBottomTab={requestBottomTab}
          />
        )}

        {ioOpen && <IoModal onClose={() => setIoOpen(false)} initialTab={ioTab} projectName={projectName} />}
        {newProjectOpen && (
          <NewProjectWizard
            existingProjects={allProjects as ProjectInfo[]}
            onConfirm={name => newProjectMut.mutate(name)}
            onClose={() => setNewProjectOpen(false)}
            isPending={newProjectMut.isPending}
          />
        )}
      </>
    )
  }

  // ── Expanded (220px) ───────────────────────────────────────────────────────
  return (
    <>
      <aside
        className="shrink-0 bg-nav border-r border-border h-full flex flex-col overflow-hidden"
        style={{ width: SIDEBAR_EXPANDED_W, minWidth: SIDEBAR_EXPANDED_W }}
      >
        {/* Brand header + collapse */}
        <div className="flex items-center justify-between shrink-0 border-b border-border"
          style={{ height: 44, paddingLeft: 14, paddingRight: 6 }}>
          <div className="flex items-center gap-2">
            <span className="text-[18px] leading-none">⚡</span>
            <span className="text-text font-bold text-[13px] tracking-tight">PyPSA Studio</span>
          </div>
          <button
            onClick={() => setSidebarMode('icon')}
            title="Collapse to icon strip  ["
            className="flex items-center justify-center text-ink-400 hover:text-text transition-colors rounded"
            style={{ width: 24, height: 24 }}
          >
            <ChevronLeft size={14} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden py-1">

          <SectionHdr title="PROJECT" open={sections.project} onToggle={() => toggleSection('project')} />
          {sections.project && (
            <ProjectSectionContent
              projectName={projectName}
              saveStatus={saveStatus} setSaveStatus={setSaveStatus}
              openIo={openIo}
              handleNewProject={handleNewProject}
            />
          )}

          <SectionHdr title="DATA" open={sections.data} onToggle={() => toggleSection('data')} />
          {sections.data && <DataSectionContent />}

          <SectionHdr title="SIMULATION" open={sections.simulation} onToggle={() => toggleSection('simulation')} />
          {sections.simulation && (
            <SimulationSectionContent requestBottomTab={requestBottomTab} />
          )}
        </div>

        <ModeSwitcher />
        <PreferencesFooter />
      </aside>

      {ioOpen && <IoModal onClose={() => setIoOpen(false)} initialTab={ioTab} projectName={projectName} />}
      {newProjectOpen && (
        <NewProjectWizard
          existingProjects={allProjects as ProjectInfo[]}
          onConfirm={name => newProjectMut.mutate(name)}
          onClose={() => setNewProjectOpen(false)}
          isPending={newProjectMut.isPending}
        />
      )}
    </>
  )
}
