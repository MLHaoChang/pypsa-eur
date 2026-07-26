import client from './client'

export type OrgRole = 'admin' | 'member'

export interface AdminOrganization {
  id: string
  name: string
}

export interface AdminUser {
  id: string
  email: string
  status: string
  is_super_admin: boolean
  org_id: string | null
  role: OrgRole | null
}

export interface CreateAdminUserPayload {
  email: string
  role: OrgRole
  org_id?: string | null
}

export interface LegacyProjectRow {
  name: string
  parent_project: string | null
  scenario_description: string | null
  has_network: boolean
  descendant_names: string[]
}

export interface ClaimLegacyProjectPayload {
  owner_id: string
  member_ids: string[]
  include_descendants: boolean
  org_id?: string | null
}

export interface ClaimedAdminProject {
  id: string
  name: string
  org_id: string
  parent_project_id: string | null
}

export interface ClaimLegacyProjectResult {
  root: ClaimedAdminProject
  claimed: ClaimedAdminProject[]
  warnings: string[]
}

export interface EmailStatus {
  configured: boolean
  smtp_host: string
  smtp_port: number
  smtp_from: string
  smtp_user_set: boolean
  delivery_mode: string
}

export interface TestEmailPayload {
  to?: string
}

export interface TestEmailResult {
  ok: true
  to: string
}

export const adminApi = {
  listOrganizations: () =>
    client.get<AdminOrganization[]>('/admin/organizations').then(r => r.data),
  createOrganization: (name: string) =>
    client.post<AdminOrganization>('/admin/organizations', { name }).then(r => r.data),
  listUsers: () =>
    client.get<AdminUser[]>('/admin/users').then(r => r.data),
  createUser: (payload: CreateAdminUserPayload) =>
    client.post<AdminUser>('/admin/users', payload).then(r => r.data),
  resendSetPassword: (userId: string) =>
    client.post<{ ok: true }>(`/admin/users/${encodeURIComponent(userId)}/resend-set-password`).then(r => r.data),
  listLegacyProjects: () =>
    client.get<LegacyProjectRow[]>('/admin/legacy-projects').then(r => r.data),
  claimLegacyProject: (legacyName: string, payload: ClaimLegacyProjectPayload) =>
    client.post<ClaimLegacyProjectResult>(
      `/admin/legacy-projects/${encodeURIComponent(legacyName)}/claim`,
      payload,
    ).then(r => r.data),
  getEmailStatus: () =>
    client.get<EmailStatus>('/admin/email/status').then(r => r.data),
  sendTestEmail: (payload: TestEmailPayload) =>
    client.post<TestEmailResult>('/admin/email/test', payload).then(r => r.data),
}
