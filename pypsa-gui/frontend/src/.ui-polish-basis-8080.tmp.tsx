
import React from 'react'
import {createRoot} from 'react-dom/client'
import CampusDipsRestartSection from './pages/CampusDipsRestartSection'
import {useUIStore} from './store/uiStore'
import './index.css'
const mode = new URLSearchParams(location.search).get('mode') === 'expert' ? 'expert' : 'guided'
useUIStore.setState({ uiMode: mode } as any)
const NONE = { ride_through_curve: null, restart_time_h: null, motor_kw: null, start_method: null, i_start_pu: null }
const dips: any = {
  inputs: { mill: { ride_through_curve: '20:0.5', restart_time_h: 4, motor_kw: 1500, start_method: 'vfd', i_start_pu: 1.2 }, pump: NONE },
  dip_label: "screening estimate; the binding answer is the operator's dip statistics and the units' tested curves",
  restart_label: 'event model, not dynamics; the binding answer is an RMS study of the restart',
  basis: ['demo dip statistics'],
  dip_trip: [{ level: 'unit', unit: 'mill', trips_per_year: 20.45, restart_time_h: 4, rate_tph: 4, mass_pull: 1, lost_t_per_trip: 16, lost_t_per_year: 327.2, verdict: 'known', dip_stats_source: 'demo', note: null }],
  restart: [], out_of_date: [],
}
createRoot(document.getElementById('root')!).render(<div style={{padding:16,maxWidth:760}}><CampusDipsRestartSection dips={dips}/></div>)
