// Dev-only harness for the UX 07 shots: the choice card alone, in the chat panel's frame.
// Imported from the signed-in SPA page (Vite gates every .html), it replaces the page body.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { ChoiceCard } from '../../src/components/ChatPanel'
import { useChatStore } from '../../src/store/chatStore'

useChatStore.setState({
  choice: {
    tool_use_id: 'tu_1', title: 'Which storage option?', question: 'The verdict flips on this one.',
    options: [{ label: 'Battery', description: 'Cheapest for a 2 h peak.', recommended: true }, { label: 'Thermal store' }],
    allow_free_text: true,
  },
})
document.body.replaceChildren()
const host = document.body.appendChild(document.createElement('div'))
createRoot(host).render(
  <div data-testid="chat-panel" className="w-[380px] bg-bg text-text"><ChoiceCard /></div>,
)
