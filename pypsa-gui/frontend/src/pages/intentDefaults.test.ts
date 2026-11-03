// AS-10 follow-up: the comparison inputs are prefilled from the network and the grid-code profile, or left blank with a reason.
import { describe, expect, it } from 'vitest'
import { intentDefaults } from './intentDefaults'
import type { Bus, Link, Load } from '../api/types'

const bus = (name: string, o: Partial<Bus> = {}) => ({ name, carrier: 'AC', ...o }) as Bus
const load = (name: string, b: string, p: number, o: Partial<Load> = {}) => ({ name, bus: b, p_set: p, ...o }) as Load
const link = (name: string, o: Partial<Link> = {}) => ({ name, bus0: 'grid', bus1: 'hub', p_min_pu: 0, ...o }) as Link

const OK = { gridCode: 'eu', status: { buses: 'ok', loads: 'ok', links: 'ok', grid: 'ok' } } as const
describe('intentDefaults', () => {
  it('sums the loads on critical buses (peak when the load has a profile)', () => {
    const d = intentDefaults({
      buses: [bus('a', { eh_critical: true }), bus('b'), bus('c', { eh_critical: true })],
      loads: [load('l1', 'a', 30), load('l2', 'b', 99), load('l3', 'c', 10, { p_set_peak: 25 })], links: [], pstLimit: null, ...OK,
    })
    expect(d.critical).toEqual({ value: '55' })
  })
  it('takes the lowest positive Sk of the PoC buses', () => {
    const d = intentDefaults({ buses: [bus('p1', { eh_poc: true, eh_sk_mva: 900 }), bus('p2', { eh_poc: true, eh_sk_mva: 600 }), bus('x', { eh_sk_mva: 10 })], loads: [], links: [], pstLimit: null, ...OK })
    expect(d.sk).toEqual({ value: '600' })
  })
  it('defaults the link to the grid_import link and lists every link', () => {
    const d = intentDefaults({ buses: [], loads: [], links: [link('z'), link('imp', { eh_role: 'grid_import' })], pstLimit: null, ...OK })
    expect(d.link).toEqual({ value: 'imp' })
    expect(d.links).toEqual(['imp', 'z'])
  })
  it('takes Pst from the profile limit', () => {
    expect(intentDefaults({ buses: [], loads: [], links: [], pstLimit: 0.8, ...OK }).pst).toEqual({ value: '0.8' })
  })
  it('leaves a field blank with a one-line reason when its source is missing', () => {
    const d = intentDefaults({ buses: [bus('a')], loads: [load('l', 'a', 5)], links: [link('z')], pstLimit: null, ...OK })
    expect(d.critical).toEqual({ value: '', reason: expect.stringContaining('critical') })
    expect(d.sk).toEqual({ value: '', reason: expect.stringContaining('point of connection') })
    expect(d.link).toEqual({ value: '', reason: expect.stringContaining('import') })
    expect(d.pst).toEqual({ value: '', reason: expect.stringContaining('Pst limit') })
  })
  it('reports critical buses with no load, and a PoC bus with no Sk, as missing', () => {
    const d = intentDefaults({ buses: [bus('a', { eh_critical: true }), bus('p', { eh_poc: true })], loads: [], links: [], pstLimit: null, ...OK })
    expect(d.critical.value).toBe('')
    expect(d.sk.value).toBe('')
  })

  it.each(['eh_import', 'import'])('treats a link tagged %s as the import link', role => {
    expect(intentDefaults({ buses: [], loads: [], links: [link('x', { eh_role: role })], pstLimit: null, ...OK }).link.value).toBe('x')
  })
  it('never shows a raw tag name in a reason', () => {
    const d = intentDefaults({ buses: [], loads: [], links: [], pstLimit: null, ...OK })
    expect(d.link.reason).not.toMatch(/grid_import|eh_/)
    expect(d.critical.reason).not.toMatch(/eh_/)
  })
  it('shows no reason while a source is loading', () => {
    const d = intentDefaults({ buses: [], loads: [], links: [], pstLimit: null, gridCode: 'eu', status: { buses: 'loading', loads: 'loading', links: 'loading', grid: 'loading' } })
    for (const k of ['critical', 'sk', 'pst', 'link'] as const) expect(d[k]).toEqual({ value: '' })
  })
  it('says what failed when a source errored, and nothing false about the rest', () => {
    const d = intentDefaults({ buses: [], loads: [], links: [], pstLimit: null, gridCode: 'eu', status: { buses: 'error', loads: 'ok', links: 'error', grid: 'error' } })
    expect(d.critical.reason).toMatch(/could not be read/i)
    expect(d.sk.reason).toMatch(/could not be read/i)
    expect(d.link.reason).toMatch(/could not be read/i)
    expect(d.pst.reason).toMatch(/could not be read/i)
  })
  it('splits the Pst reason: no grid code set, or the code sets no Pst limit', () => {
    expect(intentDefaults({ buses: [], loads: [], links: [], pstLimit: null, status: { ...OK.status, grid: 'ok' }, gridCode: null }).pst.reason).toBe('No grid code is set.')
    expect(intentDefaults({ buses: [], loads: [], links: [], pstLimit: null, ...OK }).pst.reason).toBe('The grid code sets no Pst limit.')
  })
  it('skips a load with no usable p_set instead of producing NaN', () => {
    const d = intentDefaults({ buses: [bus('a', { eh_critical: true })], loads: [load('l1', 'a', 30), load('l2', 'a', undefined as never)], links: [], pstLimit: null, ...OK })
    expect(d.critical).toEqual({ value: '30' })
  })
})
