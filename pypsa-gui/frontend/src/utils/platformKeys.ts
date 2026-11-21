/** Shortcut labels in the platform's own notation: macOS symbols, words elsewhere. */
export const isMacPlatform = (): boolean =>
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform)

/** Shift+Enter as the footer hints write it: "⇧⏎" on macOS, "Shift+⏎" elsewhere. */
export const shiftEnterLabel = (): string => (isMacPlatform() ? '⇧⏎' : 'Shift+⏎')
