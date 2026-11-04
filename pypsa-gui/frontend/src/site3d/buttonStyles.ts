// Shared Tailwind classes for the Site 3D secondary (outlined) button. A
// disabled one drops the hover border and shows the not-allowed cursor, like
// the primary buttons' disabled treatment.
// Disabled looks shared by every Site 3D button; size classes stay with the caller.
export const PRIMARY_DISABLED = 'disabled:cursor-not-allowed disabled:hover:bg-accent disabled:opacity-50'
export const SECONDARY_DISABLED = 'disabled:cursor-not-allowed disabled:hover:border-border disabled:opacity-50'
// Disabled look for Site 3D inputs, selects and link-style buttons: same dimming
// and cursor as the buttons above.
export const FIELD_DISABLED = 'disabled:cursor-not-allowed disabled:opacity-50'
export const PRIMARY_BUTTON = `px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 ${PRIMARY_DISABLED}`
export const SECONDARY_BUTTON = `px-3 py-1 rounded border border-border text-text hover:border-accent ${SECONDARY_DISABLED}`
