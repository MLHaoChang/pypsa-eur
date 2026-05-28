import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { DataGrid } from '../components/DataGrid'
import { networkApi } from '../api/network'
import type { Load } from '../api/types'

const col = createColumnHelper<Load>()
const columns = [
  col.accessor('name', { header: 'Name', size: 160 }),
  col.accessor('bus', { header: 'Bus', size: 120 }),
  col.accessor('carrier', { header: 'Carrier', size: 100 }),
  col.accessor('p_set', { header: 'p_set (MW)', size: 110 }),
  col.accessor('q_set', { header: 'q_set (MVAr)', size: 110 }),
  col.accessor('sign', { header: 'Sign', size: 60 }),
]

export default function LoadEditor() {
  const qc = useQueryClient()
  const { data: loads = [], isLoading } = useQuery({ queryKey: ['loads'], queryFn: networkApi.getLoads })
  const del = useMutation({
    mutationFn: (l: Load) => networkApi.deleteLoad(l.name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['loads'] }),
  })

  // Aggregate load per bus for the chart
  const busAgg: Record<string, number> = {}
  loads.forEach(l => { busAgg[l.bus] = (busAgg[l.bus] ?? 0) + Math.abs(l.p_set) })
  const chartData = Object.entries(busAgg).map(([bus, p_set]) => ({ bus, p_set: parseFloat(p_set.toFixed(2)) }))

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-hidden min-h-0">
        <DataGrid
          columns={columns as Parameters<typeof DataGrid>[0]['columns']}
          data={loads as unknown as Record<string, unknown>[]}
          isLoading={isLoading}
          onDelete={(row) => del.mutate(row as unknown as Load)}
        />
      </div>

      {chartData.length > 0 && (
        <div className="shrink-0 border-t border-border p-3" style={{ height: 180 }}>
          <p className="text-xs text-muted mb-2">Load per Bus (MW)</p>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 0, right: 8, bottom: 24, left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
              <XAxis dataKey="bus" tick={{ fill: '#8b949e', fontSize: 10 }} />
              <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} />
              <Tooltip contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 11 }} />
              <Bar dataKey="p_set" fill="#2f81f7" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
