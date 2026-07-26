import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { adminApi } from '../../api/admin'
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

const ORGS_QUERY_KEY = ['admin', 'organizations']
const INPUT_CLASS_NAME = 'w-full rounded-xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-3 py-2 text-sm text-[var(--brand-ink)] outline-none transition focus:border-[var(--brand-red)] focus:ring-2 focus:ring-[rgba(255,82,82,0.28)]'

export default function OrgsPage() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [name, setName] = useState('')

  const { data: organizations = [], isLoading } = useQuery({
    queryKey: ORGS_QUERY_KEY,
    queryFn: adminApi.listOrganizations,
    staleTime: 10_000,
  })

  const createOrganization = useMutation({
    mutationFn: () => adminApi.createOrganization(name.trim()),
    onSuccess: async (organization) => {
      toast.success(`Created ${organization.name}.`)
      setName('')
      await queryClient.invalidateQueries({ queryKey: ORGS_QUERY_KEY })
    },
  })

  const sortedOrganizations = useMemo(
    () => [...organizations].sort((left, right) => left.name.localeCompare(right.name)),
    [organizations],
  )
  const canCreateOrganizations = Boolean(user?.is_super_admin)

  function handleCreateOrganization(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canCreateOrganizations || !name.trim()) return
    createOrganization.mutate()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        eyebrow="Admin / organizations"
        title="Organizations"
        subtitle="Review tenant boundaries across the system. Super-admins can create new organizations; org admins see a read-only view of their own organization."
      />
      <PageBody>
        <RowGrid cols={4}>
          <StatCard accent eyebrow="Visible orgs" value={sortedOrganizations.length} sub="Scoped by tenancy access" />
          <StatCard eyebrow="Your role" value={user?.is_super_admin ? 'Global' : 'Scoped'} sub={user?.is_super_admin ? 'Super-admin permissions' : 'Single-organization access'} />
          <StatCard eyebrow="Create access" value={canCreateOrganizations ? 'Yes' : 'No'} sub="Organization creation endpoint" />
          <StatCard eyebrow="Current org" value={user?.org_id ? 'Attached' : 'None'} sub={user?.org_id ?? 'No organization membership'} />
        </RowGrid>

        <PageSection title="Create organization" hint="Only super-admins can create organizations.">
          {canCreateOrganizations ? (
            <form className="flex flex-col gap-4 md:flex-row md:items-end" onSubmit={handleCreateOrganization}>
              <Field label="Name">
                <input
                  className={INPUT_CLASS_NAME}
                  onChange={event => setName(event.target.value)}
                  placeholder="Northwind Energy"
                  type="text"
                  value={name}
                />
              </Field>
              <Btn disabled={!name.trim() || createOrganization.isPending} type="submit" variant="primary">
                {createOrganization.isPending ? 'Creating…' : 'Create organization'}
              </Btn>
            </form>
          ) : (
            <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-4 text-sm text-[var(--brand-ink-dim)]">
              Your account can inspect organization metadata but cannot create new organizations.
            </div>
          )}
        </PageSection>

        <PageSection title="Directory" count={sortedOrganizations.length}>
          {isLoading ? (
            <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
              Loading organizations…
            </div>
          ) : sortedOrganizations.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
              No organizations are visible to this account yet.
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {sortedOrganizations.map((organization, index) => (
                <article
                  key={organization.id}
                  className="rounded-3xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-5 py-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-semibold tracking-[-0.02em] text-[var(--brand-ink)]">
                        {organization.name}
                      </h2>
                      <p className="mt-1 font-mono text-[11px] text-[var(--brand-ink-dim)]">{organization.id}</p>
                    </div>
                    <Tag tone={index === 0 && !user?.is_super_admin ? 'purple' : 'neutral'}>
                      {user?.org_id === organization.id ? 'Current org' : 'Visible'}
                    </Tag>
                  </div>
                </article>
              ))}
            </div>
          )}
        </PageSection>
      </PageBody>
    </div>
  )
}
