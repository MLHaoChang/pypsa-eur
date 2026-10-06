import { describe, expect, it } from 'vitest'
import {
  draftNeeds, fillDraft, initialConfig, ownerOf, payeeOf, problemSection, setOwner, setPayee,
} from './designerModel'

const CTX = { site_party: 'site', assets: [], tariff_items: [], contract_parties: [],
              group_members: [], default_externals: ['retailer', 'dso'] }

describe('designer model', () => {
  it('starts from the stored config only when it validates', () => {
    const stored = { template: 'btm_ppa' as const,
                     participants: [{ id: 'site', name: 'Site', role: 'offtaker' as const }] }
    expect(initialConfig({ value_flows: stored, digest: 'd', status: 'ok' }, CTX)).toEqual(stored)
    const fresh = initialConfig({ value_flows: null, digest: 'd', status: 'not_set' }, CTX)
    expect(fresh.participants).toEqual([{ id: 'site', name: 'site', role: 'site_owner' }])
    expect(fresh.externals).toEqual(['retailer', 'dso'])
    expect(initialConfig({ value_flows: { participants: 'x' } as never, digest: 'd',
                           status: 'value_flows_invalid' }, CTX).template).toBe('custom')
  })

  it('sets and clears an owner and a payee, keeping the template stamp', () => {
    let cfg = { template: 'single_owner' as const, built_digest: 'b' }
    cfg = setOwner(cfg, 'Generator', 'pv', 'dev') as typeof cfg
    expect(ownerOf(cfg, 'Generator', 'pv')).toBe('dev')
    expect(cfg.template).toBe('single_owner')                 // the server says template_edited
    cfg = setOwner(cfg, 'Generator', 'pv', '') as typeof cfg
    expect(ownerOf(cfg, 'Generator', 'pv')).toBe('')
    const withPayee = setPayee(cfg, 'energy', 'dso')
    expect(payeeOf(withPayee, 'energy')).toBe('dso')
    expect(payeeOf(setPayee(withPayee, 'energy', ''), 'energy')).toBe('')
  })

  it('prices a draft only when every null field is a number ≥ 0', () => {
    const draft = { type: 'dr', id: 'dr_draft', availability_eur_per_mw_year: null,
                    activation_eur_per_mwh: null, contracted_mw: null, load_ids: ['l'] }
    expect(draftNeeds(draft)).toEqual(['availability_eur_per_mw_year', 'activation_eur_per_mwh',
                                       'contracted_mw'])
    expect(fillDraft(draft, { availability_eur_per_mw_year: '10' })).toBeNull()
    expect(fillDraft(draft, { availability_eur_per_mw_year: '10', activation_eur_per_mwh: '-1',
                              contracted_mw: '2' })).toBeNull()
    expect(fillDraft(draft, { availability_eur_per_mw_year: '10', activation_eur_per_mwh: '100',
                              contracted_mw: '2' }))
      .toMatchObject({ availability_eur_per_mw_year: 10, contracted_mw: 2, load_ids: ['l'] })
  })

  it('routes on the server\'s phrasing, never on a name inside it', () => {
    expect(problemSection("contract 'c' generator_owner 'Owner Co' is neither a participant nor an external")).toBe('participants')
    expect(problemSection("contract 'group_ppa' seller 'x' is neither a participant nor an external")).toBe('participants')
    expect(problemSection("participant 'item 1' is also an external")).toBe('participants')
    expect(problemSection("Generator 'pv' is owned twice")).toBe('assets')
    expect(problemSection('hub_members need a group contract')).toBe('hub')
    expect(problemSection('the contracted_capacity key needs contracted_mw on every hub member')).toBe('hub')
  })

  it('places a server problem beside its section', () => {
    expect(problemSection("asset 'ghost' is not a Generator of the network")).toBe('assets')
    expect(problemSection("tariff payee 'x' is neither a participant nor an external")).toBe('payees')
    expect(problemSection('hub member link \'l\' is not a group member')).toBe('hub')
    expect(problemSection("an allocation key needs every group member as a hub member")).toBe('hub')
    expect(problemSection("participant 'retailer' is also an external")).toBe('participants')
  })
})
