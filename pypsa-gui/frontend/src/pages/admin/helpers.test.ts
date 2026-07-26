import { describe, expect, it } from 'vitest'
import type { AuthUser } from '../../api/auth'
import type { AdminUser } from '../../api/admin'
import {
  getAdminDefaultPath,
  hasAdminConsoleAccess,
  sortAdminUsers,
  usersForOrganization,
} from './helpers'

const superAdmin: AuthUser = {
  id: 'super-1',
  email: 'owner@example.com',
  status: 'active',
  is_super_admin: true,
  org_id: null,
  role: null,
}

const orgAdmin: AuthUser = {
  id: 'admin-1',
  email: 'admin@example.com',
  status: 'active',
  is_super_admin: false,
  org_id: 'org-1',
  role: 'admin',
}

const member: AuthUser = {
  id: 'member-1',
  email: 'member@example.com',
  status: 'active',
  is_super_admin: false,
  org_id: 'org-1',
  role: 'member',
}

const users: AdminUser[] = [
  {
    id: 'user-2',
    email: 'zoe@example.com',
    status: 'invited',
    is_super_admin: false,
    org_id: 'org-1',
    role: 'member',
  },
  {
    id: 'user-1',
    email: 'amy@example.com',
    status: 'active',
    is_super_admin: false,
    org_id: 'org-2',
    role: 'admin',
  },
  {
    id: 'user-3',
    email: 'bea@example.com',
    status: 'disabled',
    is_super_admin: false,
    org_id: 'org-1',
    role: 'admin',
  },
]

describe('hasAdminConsoleAccess', () => {
  it('admits super-admins and org admins only', () => {
    expect(hasAdminConsoleAccess(superAdmin)).toBe(true)
    expect(hasAdminConsoleAccess(orgAdmin)).toBe(true)
    expect(hasAdminConsoleAccess(member)).toBe(false)
    expect(hasAdminConsoleAccess(null)).toBe(false)
  })
})

describe('getAdminDefaultPath', () => {
  it('routes super-admins to user management and org admins to organizations', () => {
    expect(getAdminDefaultPath(superAdmin)).toBe('/admin/users')
    expect(getAdminDefaultPath(orgAdmin)).toBe('/admin/organizations')
  })
})

describe('usersForOrganization', () => {
  it('returns only users in the requested organization', () => {
    expect(usersForOrganization(users, 'org-1').map(user => user.email)).toEqual([
      'zoe@example.com',
      'bea@example.com',
    ])
  })
})

describe('sortAdminUsers', () => {
  it('sorts users by organization, role priority, then email', () => {
    expect(sortAdminUsers(users).map(user => user.email)).toEqual([
      'bea@example.com',
      'zoe@example.com',
      'amy@example.com',
    ])
  })
})
