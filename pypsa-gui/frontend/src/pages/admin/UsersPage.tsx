import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { adminApi, type CreateAdminUserPayload, type OrgRole } from '../../api/admin'
import { useAuth } from '../../auth/AuthProvider'
import {
  Btn,
  Field,
  PageBody,
  PageHeader,
  PageSection,
  RowGrid,
  StatCard,
  Tag,
} from '../../components/PageKit'
import { sortAdminUsers } from './helpers'

const USERS_QUERY_KEY = ['admin', 'users']
const ORGS_QUERY_KEY = ['admin', 'organizations']
const INPUT_CLASS_NAME = 'w-full rounded-xl border border-[#d6e1d8] bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-[#8ca794] focus:ring-2 focus:ring-[#d8e6db]'

function roleTone(role: string | null): 'accent' | 'purple' | 'neutral' {
  if (role === 'admin') return 'purple'
  if (role === 'member') return 'accent'
  return 'neutral'
}

function statusTone(status: string): 'ok' | 'warn' | 'err' | 'neutral' {
  if (status === 'active') return 'ok'
  if (status === 'invited') return 'warn'
  if (status === 'disabled') return 'err'
  return 'neutral'
}

export default function UsersPage() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<OrgRole>('member')
  const [orgId, setOrgId] = useState('')

  const { data: users = [], isLoading: isLoadingUsers } = useQuery({
    queryKey: USERS_QUERY_KEY,
    queryFn: adminApi.listUsers,
    staleTime: 10_000,
  })
  const { data: organizations = [] } = useQuery({
    queryKey: ORGS_QUERY_KEY,
    queryFn: adminApi.listOrganizations,
    staleTime: 10_000,
  })

  useEffect(() => {
    if (!user) return
    if (!user.is_super_admin) {
      setOrgId(user.org_id ?? '')
      return
    }
    if (!orgId) {
      setOrgId(user.org_id ?? organizations[0]?.id ?? '')
    }
  }, [orgId, organizations, user])

  const sortedUsers = useMemo(() => sortAdminUsers(users), [users])
  const organizationsById = useMemo(
    () => new Map(organizations.map(organization => [organization.id, organization.name])),
    [organizations],
  )
  const invitedCount = sortedUsers.filter(row => row.status === 'invited').length
  const orgAdminCount = sortedUsers.filter(row => row.role === 'admin').length
  const representedOrgCount = new Set(sortedUsers.map(row => row.org_id).filter(Boolean)).size

  const createUser = useMutation({
    mutationFn: async () => {
      const payload: CreateAdminUserPayload = {
        email: email.trim(),
        role,
      }
      if (user?.is_super_admin) {
        payload.org_id = orgId || null
      }
      return adminApi.createUser(payload)
    },
    onSuccess: async (createdUser) => {
      toast.success(`Invitation sent to ${createdUser.email}.`)
      setEmail('')
      setRole('member')
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY })
    },
  })

  const resendInvite = useMutation({
    mutationFn: (userId: string) => adminApi.resendSetPassword(userId),
    onSuccess: () => {
      toast.success('Set-password email re-sent.')
    },
  })

  const canInvite = email.trim().length > 0 && (!user?.is_super_admin || Boolean(orgId))

  function handleCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canInvite) return
    createUser.mutate()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        eyebrow="Admin / users"
        title="Users"
        subtitle="Invite new users into an organization, monitor status, and re-send password setup links when onboarding stalls."
      />
      <PageBody>
        <RowGrid cols={4}>
          <StatCard accent eyebrow="Visible users" value={sortedUsers.length} sub="Scoped by your admin permissions" />
          <StatCard eyebrow="Invited" value={invitedCount} sub="Pending password setup" />
          <StatCard eyebrow="Org admins" value={orgAdminCount} sub="Users with elevated org access" />
          <StatCard eyebrow="Organizations" value={representedOrgCount} sub="Represented in this view" />
        </RowGrid>

        <PageSection
          title="Invite user"
          hint={user?.is_super_admin ? 'Super-admins can invite into any organization.' : 'Org admins can invite users only into their own organization.'}
        >
          <form className="grid gap-4 md:grid-cols-[minmax(0,2fr)_180px_220px_auto]" onSubmit={handleCreateUser}>
            <Field label="Email">
              <input
                className={INPUT_CLASS_NAME}
                onChange={event => setEmail(event.target.value)}
                placeholder="new.user@example.com"
                type="email"
                value={email}
              />
            </Field>
            <Field label="Role">
              <select
                className={INPUT_CLASS_NAME}
                onChange={event => setRole(event.target.value as OrgRole)}
                value={role}
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
            <Field label="Organization">
              <select
                className={INPUT_CLASS_NAME}
                disabled={!user?.is_super_admin || organizations.length === 0}
                onChange={event => setOrgId(event.target.value)}
                value={orgId}
              >
                {user?.is_super_admin && <option value="">Select organization…</option>}
                {organizations.map(organization => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </Field>
            <div className="flex items-end">
              <Btn disabled={!canInvite || createUser.isPending || (user?.is_super_admin && organizations.length === 0)} type="submit" variant="primary">
                {createUser.isPending ? 'Inviting…' : 'Invite user'}
              </Btn>
            </div>
          </form>
          {user?.is_super_admin && organizations.length === 0 && (
            <p className="mt-3 text-sm text-slate-600">
              Create an organization first before inviting users.
            </p>
          )}
        </PageSection>

        <PageSection
          title="Directory"
          count={sortedUsers.length}
          hint="Rows reflect the backend tenancy filters for your account."
        >
          {isLoadingUsers ? (
            <div className="rounded-2xl border border-dashed border-[#d6e1d8] bg-[#fbfdfb] px-5 py-8 text-sm text-slate-500">
              Loading users…
            </div>
          ) : sortedUsers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-[#d6e1d8] bg-[#fbfdfb] px-5 py-8 text-sm text-slate-600">
              No users are visible for this scope yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full border-separate border-spacing-y-2 text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                    <th className="px-3 pb-1 font-semibold">Email</th>
                    <th className="px-3 pb-1 font-semibold">Organization</th>
                    <th className="px-3 pb-1 font-semibold">Role</th>
                    <th className="px-3 pb-1 font-semibold">Status</th>
                    <th className="px-3 pb-1 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUsers.map(row => (
                    <tr key={row.id} className="rounded-2xl bg-[#f8fbf8] shadow-[inset_0_0_0_1px_#e5ede7]">
                      <td className="rounded-l-2xl px-3 py-3 align-middle">
                        <div className="font-medium text-slate-900">{row.email}</div>
                        {row.is_super_admin && (
                          <div className="mt-1 text-xs text-slate-500">Super-admin</div>
                        )}
                      </td>
                      <td className="px-3 py-3 align-middle text-slate-700">
                        {row.org_id ? organizationsById.get(row.org_id) ?? row.org_id : '—'}
                      </td>
                      <td className="px-3 py-3 align-middle">
                        <Tag tone={roleTone(row.role)}>{row.role ?? 'none'}</Tag>
                      </td>
                      <td className="px-3 py-3 align-middle">
                        <Tag tone={statusTone(row.status)}>{row.status}</Tag>
                      </td>
                      <td className="rounded-r-2xl px-3 py-3 align-middle text-right">
                        <Btn
                          disabled={resendInvite.isPending && resendInvite.variables === row.id}
                          onClick={() => resendInvite.mutate(row.id)}
                          type="button"
                        >
                          {resendInvite.isPending && resendInvite.variables === row.id
                            ? 'Sending…'
                            : 'Resend set-password'}
                        </Btn>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </PageSection>
      </PageBody>
    </div>
  )
}
