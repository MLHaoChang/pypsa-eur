import { describe, expect, it } from 'vitest'

// Assistant-assurance 06 (A5.3, GK G2): every href that assistant prose or a chat
// card renders goes through the one seam, `approvedChatHref`. The card kinds are
// read from what ChatPanel imports, not from a hand list, so a new card that
// renders a link is covered the day ChatPanel imports it.
const sources = import.meta.glob('./*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

// ChatPanel and every component module it reaches through `./` imports.
const chatModules: string[] = []
for (const queue = ['./ChatPanel.tsx']; queue.length;) {
  const path = queue.shift()!
  if (chatModules.includes(path) || !(path in sources)) continue
  chatModules.push(path)
  // `[^'"]*` spans a multi-line import list and stops at the module string.
  for (const m of sources[path].matchAll(/^import\s[^'"]*from '\.\/([A-Za-z0-9]+)'/gm)) queue.push(`./${m[1]}.tsx`)
}

describe('chat link seam', () => {
  it('reads the chat card modules from ChatPanel', () => {
    expect(chatModules).toEqual(expect.arrayContaining(['./ChatMarkdown.tsx', './AssistantResultCard.tsx', './CheckedEvidenceCard.tsx', './EditCard.tsx', './CheckedChart.tsx']))
  })

  it.each(chatModules)('%s builds no href outside the seam', (path) => {
    const code = sources[path]
    if (!/\bhref=/.test(code)) return
    expect(code).toMatch(/import \{[^}]*\bapprovedChatHref\b[^}]*\} from '\.\.\/utils\/chatLinks'/)
    // A literal or template href would skip the seam.
    expect(code).not.toMatch(/\bhref=\s*["'`]/)
    expect(code).not.toMatch(/\bhref=\{\s*`/)
  })
})
