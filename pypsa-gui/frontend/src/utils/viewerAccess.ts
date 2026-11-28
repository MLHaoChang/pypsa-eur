// The viewer role's words (project-viewer-role 05). The cause sentence is the
// backend's refusal (`services/project_write_gate.MESSAGE`) word for word, so a
// viewer who clicks, or asks the assistant to edit, reads the same thing; a
// backend test pins the two together.
export const VIEWER_CAUSE = 'You can view this project.'

// A toast or hover hint: the owner is not on hand, so it says "its owner".
export const VIEWER_MUTATION_MESSAGE = `${VIEWER_CAUSE} Ask its owner for edit access.`

// The banner line: names the owner by email when it is known (users have no
// display name).
export function viewerBannerMessage(ownerEmail: string | null): string {
  return `${VIEWER_CAUSE} Ask ${ownerEmail ?? 'its owner'} for edit access.`
}

export interface AccessFields {
  my_access?: string | null
  can_manage_members?: boolean | null
  owner_email?: string | null
}
