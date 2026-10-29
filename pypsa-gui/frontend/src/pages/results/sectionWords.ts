/** A Results section key by its plain name: "results_capacity" → "Capacity". */
export function sectionWords(section: string): string {
  const w = section.replace(/^results_/, '').replace(/_/g, ' ').trim()
  return w ? w.charAt(0).toUpperCase() + w.slice(1) : section
}
