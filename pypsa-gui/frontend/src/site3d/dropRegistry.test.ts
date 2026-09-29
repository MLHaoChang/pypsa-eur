import { describe, it, expect, beforeEach } from 'vitest'
import { registerSiteDropTarget, unregisterSiteDropTarget, siteDropTarget, type SiteDropTarget } from './dropRegistry'

const t = (siteId: string): SiteDropTarget => ({ siteId, screenToGround: () => ({ x: 1, y: 2 }), groundToScreen: () => ({ x: 3, y: 4 }) })

describe('site drop registry', () => {
  beforeEach(() => { const cur = siteDropTarget(); if (cur) unregisterSiteDropTarget(cur) })

  it('is empty until a canvas registers, and empty again after it unregisters', () => {
    expect(siteDropTarget()).toBeNull()
    const a = t('a')
    registerSiteDropTarget(a)
    expect(siteDropTarget()).toBe(a)
    unregisterSiteDropTarget(a)
    expect(siteDropTarget()).toBeNull()
  })

  it('a stale unmount does not clear a newer mount', () => {
    const a = t('a'), b = t('b')
    registerSiteDropTarget(a)
    registerSiteDropTarget(b)
    unregisterSiteDropTarget(a)
    expect(siteDropTarget()).toBe(b)
    unregisterSiteDropTarget(b)
  })
})
