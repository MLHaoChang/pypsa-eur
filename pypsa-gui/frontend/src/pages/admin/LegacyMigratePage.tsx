import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { adminApi, type ClaimLegacyProjectResult } from '../../api/admin'
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
import { usersForOrganization } from './helpers'
import { scenDescriptionText } from '../../utils/scenarioType'

const LEGACY_QUERY_KEY = ['admin', 'legacy-projects']
const USERS_QUERY_KEY = ['admin', 'users']
const ORGS_QUERY_KEY = ['admin', 'organizations']
const INPUT_CLASS_NAME = 'w-full rounded-xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-3 py-2 text-sm text-[var(--brand-ink)] outline-none transition focus:border-[var(--brand-red)] focus:ring-2 focus:ring-[rgba(255,82,82,0.28)]'

export default function LegacyMigratePage() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [selectedLegacyName, setSelectedLegacyName] = useState('')
  const [targetOrgId, setTargetOrgId] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [memberIds, setMemberIds] = useState<string[]>([])
  const [includeDescendants, setIncludeDescendants] = useState(true)
  const [lastResult, setLastResult] = useState<ClaimLegacyProjectResult | null>(null)

  const { data: legacyProjects = [], isLoading: isLoadingLegacy } = useQuery({
    queryKey: LEGACY_QUERY_KEY,
    queryFn: adminApi.listLegacyProjects,
    staleTime: 10_000,
  })
  const { data: users = [] } = useQuery({
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
    if (!legacyProjects.length) {
      setSelectedLegacyName('')
      return
    }
    if (!legacyProjects.some(project => project.name === selectedLegacyName)) {
      setSelectedLegacyName(legacyProjects[0].name)
    }
  }, [legacyProjects, selectedLegacyName])

  useEffect(() => {
    if (!user) return
    if (!user.is_super_admin) {
      setTargetOrgId(user.org_id ?? '')
      return
    }
    setTargetOrgId(current => current || user.org_id || organizations[0]?.id || '')
  }, [organizations, user])

  const selectedLegacy = legacyProjects.find(project => project.name === selectedLegacyName) ?? null
  const targetOrganizationName = organizations.find(organization => organization.id === targetOrgId)?.name ?? targetOrgId
  const assignableUsers = useMemo(
    () => usersForOrganization(users, targetOrgId).sort((left, right) => left.email.localeCompare(right.email)),
    [targetOrgId, users],
  )
  const assignableUserIds = useMemo(() => new Set(assignableUsers.map(candidate => candidate.id)), [assignableUsers])

  useEffect(() => {
    if (ownerId && assignableUserIds.has(ownerId)) return
    setOwnerId(assignableUsers[0]?.id ?? '')
  }, [assignableUserIds, assignableUsers, ownerId])

  useEffect(() => {
    setMemberIds(current => current.filter(id => id !== ownerId && assignableUserIds.has(id)))
  }, [assignableUserIds, ownerId])

  const claimLegacy = useMutation({
    mutationFn: async () => {
      if (!selectedLegacy) {
        throw new Error('Select a legacy project first.')
      }
      return adminApi.claimLegacyProject(selectedLegacy.name, {
        owner_id: ownerId,
        member_ids: memberIds.filter(id => id !== ownerId),
        include_descendants: includeDescendants,
        ...(user?.is_super_admin ? { org_id: targetOrgId || null } : {}),
      })
    },
    onSuccess: async (result) => {
      setLastResult(result)
      toast.success(`Claimed ${result.claimed.length} project${result.claimed.length === 1 ? '' : 's'}.`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: LEGACY_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
      ])
    },
  })

  const descendantHeavyCount = legacyProjects.filter(project => project.descendant_names.length > 0).length
  const networkBackedCount = legacyProjects.filter(project => project.has_network).length
  const canClaim = Boolean(selectedLegacy && ownerId && (!user?.is_super_admin || targetOrgId))

  function handleMemberSelection(event: FormEvent<HTMLSelectElement>) {
    const nextIds = Array.from(event.currentTarget.selectedOptions, option => option.value)
      .filter(id => id !== ownerId)
    setMemberIds(nextIds)
  }

  function handleClaim(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canClaim) return
    claimLegacy.mutate()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        eyebrow="Admin / legacy migrate"
        title="Legacy migrate"
        subtitle="Inspect unclaimed legacy projects on disk, choose the destination organization, and claim a root project with optional descendants in one operation."
      />
      <PageBody>
        <RowGrid cols={4}>
          <StatCard accent eyebrow="Unclaimed roots" value={legacyProjects.length} sub="Legacy projects still outside tenancy" />
          <StatCard eyebrow="With descendants" value={descendantHeavyCount} sub="Projects that have scenario children" />
          <StatCard eyebrow="With network files" value={networkBackedCount} sub="Ready to import into tenant storage" />
          <StatCard eyebrow="Target org" value={targetOrganizationName || 'Unset'} sub={user?.is_super_admin ? 'Choose per claim' : 'Bound to your organization'} />
        </RowGrid>

        <div className="grid min-h-0 gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <PageSection title="Legacy inventory" count={legacyProjects.length}>
            {isLoadingLegacy ? (
              <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
                Loading legacy projects…
              </div>
            ) : legacyProjects.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
                No unclaimed legacy projects were found.
              </div>
            ) : (
              <div className="grid gap-3">
                {legacyProjects.map(project => {
                  const isSelected = project.name === selectedLegacyName
                  return (
                    <button
                      key={project.name}
                      className={`rounded-3xl border px-5 py-4 text-left transition ${
                        isSelected
                          ? 'border-[var(--brand-red)] bg-[var(--brand-surface-2)] shadow-[0_0_0_3px_rgba(140,167,148,0.14)]'
                          : 'border-[var(--brand-line)] bg-[var(--brand-surface-2)] hover:border-[var(--brand-line)]'
                      }`}
                      onClick={() => setSelectedLegacyName(project.name)}
                      type="button"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-semibold tracking-[-0.02em] text-[var(--brand-ink)]">
                          {project.name}
                        </h2>
                        <Tag tone={project.has_network ? 'ok' : 'warn'}>
                          {project.has_network ? 'network ready' : 'metadata only'}
                        </Tag>
                        {project.descendant_names.length > 0 && (
                          <Tag tone="purple">{project.descendant_names.length} descendants</Tag>
                        )}
                      </div>
                      <div className="mt-2 space-y-1 text-sm text-[var(--brand-ink-dim)]">
                        <p>Parent: {project.parent_project ?? 'Root project'}</p>
                        {/* Stripped, not raw: the `[type]` marker the scenario
                            dialog prefixes is an encoding, not prose. */}
                        {scenDescriptionText(project.scenario_description) && (
                          <p>{scenDescriptionText(project.scenario_description)}</p>
                        )}
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </PageSection>

          <PageSection title="Claim selected project" hint="Owner and members must belong to the destination organization.">
            {!selectedLegacy ? (
              <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
                Select a legacy project from the inventory to prepare the claim payload.
              </div>
            ) : (
              <form className="space-y-4" onSubmit={handleClaim}>
                <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-4 py-3">
                  <div className="text-sm font-medium text-[var(--brand-ink)]">{selectedLegacy.name}</div>
                  <div className="mt-1 text-sm text-[var(--brand-ink-dim)]">
                    Parent: {selectedLegacy.parent_project ?? 'Root project'} · Descendants: {selectedLegacy.descendant_names.length}
                  </div>
                </div>

                <Field label="Destination organization">
                  <select
                    className={INPUT_CLASS_NAME}
                    disabled={!user?.is_super_admin}
                    onChange={event => setTargetOrgId(event.target.value)}
                    value={targetOrgId}
                  >
                    {user?.is_super_admin && <option value="">Select organization…</option>}
                    {organizations.map(organization => (
                      <option key={organization.id} value={organization.id}>
                        {organization.name}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="Owner">
                  <select
                    className={INPUT_CLASS_NAME}
                    disabled={!targetOrgId || assignableUsers.length === 0}
                    onChange={event => setOwnerId(event.target.value)}
                    value={ownerId}
                  >
                    <option value="">Select owner…</option>
                    {assignableUsers.map(candidate => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.email} ({candidate.role ?? 'no role'})
                      </option>
                    ))}
                  </select>
                </Field>

                <Field
                  hint="Use Ctrl/Cmd-click to select multiple members."
                  label="Additional members"
                >
                  <select
                    className={INPUT_CLASS_NAME}
                    disabled={!targetOrgId || assignableUsers.length === 0}
                    multiple
                    onChange={handleMemberSelection}
                    size={Math.min(6, Math.max(3, assignableUsers.length))}
                    value={memberIds}
                  >
                    {assignableUsers
                      .filter(candidate => candidate.id !== ownerId)
                      .map(candidate => (
                        <option key={candidate.id} value={candidate.id}>
                          {candidate.email} ({candidate.role ?? 'no role'})
                        </option>
                      ))}
                  </select>
                </Field>

                <label className="flex items-center gap-3 rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface)] px-4 py-3 text-sm text-[var(--brand-ink-dim)]">
                  <input
                    checked={includeDescendants}
                    onChange={event => setIncludeDescendants(event.target.checked)}
                    type="checkbox"
                  />
                  Include descendant projects in the claim operation.
                </label>

                {targetOrgId && assignableUsers.length === 0 && (
                  <div className="rounded-2xl border border-[rgba(246,198,138,0.35)] bg-[rgba(246,198,138,0.10)] px-4 py-3 text-sm text-[var(--brand-warn)]">
                    No users are available in the target organization yet, so a claim owner cannot be chosen.
                  </div>
                )}

                <Btn disabled={!canClaim || claimLegacy.isPending} type="submit" variant="primary">
                  {claimLegacy.isPending ? 'Claiming…' : 'Claim project'}
                </Btn>
              </form>
            )}

            {lastResult && (
              <div className="mt-4 rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface)] px-4 py-4">
                <div className="text-sm font-medium text-[var(--brand-ink)]">
                  Last claim imported {lastResult.claimed.length} project{lastResult.claimed.length === 1 ? '' : 's'}.
                </div>
                <div className="mt-2 text-sm text-[var(--brand-ink-dim)]">
                  Root: {lastResult.root.name}
                </div>
                {lastResult.warnings.length > 0 && (
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-[var(--brand-warn)]">
                    {lastResult.warnings.map(warning => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </PageSection>
        </div>
      </PageBody>
    </div>
  )
}
