import type { AuthUser } from '../../api/auth'
import type { AdminUser } from '../../api/admin'

const ROLE_ORDER: Record<string, number> = {
  admin: 0,
  member: 1,
}

export function hasAdminConsoleAccess(user: Pick<AuthUser, 'is_super_admin' | 'role'> | null | undefined): boolean {
  return Boolean(user?.is_super_admin || user?.role === 'admin')
}

export function getAdminDefaultPath(user: Pick<AuthUser, 'is_super_admin'> | null | undefined): string {
  return user?.is_super_admin ? '/admin/users' : '/admin/organizations'
}

export function usersForOrganization(users: AdminUser[], orgId: string | null | undefined): AdminUser[] {
  if (!orgId) return []
  return users.filter(user => user.org_id === orgId)
}

export function sortAdminUsers(users: AdminUser[]): AdminUser[] {
  return [...users].sort((left, right) => {
    const orgCompare = (left.org_id ?? '').localeCompare(right.org_id ?? '')
    if (orgCompare !== 0) return orgCompare

    const roleCompare = (ROLE_ORDER[left.role ?? 'member'] ?? 9) - (ROLE_ORDER[right.role ?? 'member'] ?? 9)
    if (roleCompare !== 0) return roleCompare

    return left.email.localeCompare(right.email)
  })
}
