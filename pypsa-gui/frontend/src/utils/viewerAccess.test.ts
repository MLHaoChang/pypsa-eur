import { describe, expect, it } from 'vitest'
import { readOnlyBannerMessage, readOnlyMessage } from './mutationGuard'
import { useUIStore } from '../store/uiStore'

describe('viewer access words', () => {
  it('names the owner in the banner, one line, two sentences', () => {
    expect(readOnlyBannerMessage('viewer', null, 'o@x.com'))
      .toBe('You can view this project. Ask o@x.com for edit access.')
    expect(readOnlyBannerMessage('viewer')).toBe('You can view this project. Ask its owner for edit access.')
  })
  it('uses the backend refusal wording in a toast', () => {
    expect(readOnlyMessage('viewer')).toBe('You can view this project. Ask its owner for edit access.')
  })
  it('setProjectAccess makes a viewer read-only and a later lock cannot clear it', () => {
    const s = useUIStore.getState()
    s.setProjectAccess({ my_access: 'view', owner_email: 'o@x.com', can_manage_members: false })
    expect(useUIStore.getState().readOnlyReason).toBe('viewer')
    s.setLockState({ readOnly: false, holderEmail: null, reason: 'writable' })
    expect(useUIStore.getState().readOnly).toBe(true)
    s.setProjectAccess({ my_access: 'edit', owner_email: 'o@x.com', can_manage_members: true })
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().canManageMembers).toBe(true)
  })
})
