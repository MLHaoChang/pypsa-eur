import { createPortal } from 'react-dom'
import { useChatAnnouncer } from '../store/chatAnnouncer'

/** The chat's shared polite live region (A5.5); mounted once by ChatPanel. It is
 * portalled to the body so a collapsed dock (a hidden panel) cannot silence it. */
export function ChatAnnouncer() {
  const text = useChatAnnouncer(s => s.text)
  return createPortal(
    <div role="status" aria-live="polite" aria-atomic="true" className="sr-only" data-testid="chat-announcer">{text}</div>,
    document.body)
}
