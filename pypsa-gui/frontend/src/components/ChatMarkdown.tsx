// Styled markdown renderer for assistant chat messages.
//
// The chatbot emits rich GitHub-flavored markdown (tables, **bold**, ## headers,
// bullet/numbered lists, `code`). Rendered as raw text it collapses into a wall
// of literal `*`/`|`/`#` symbols (newlines also collapse without pre-wrap), which
// is hard to read. This wraps react-markdown + remark-gfm with the app's dark-
// theme Tailwind tokens, kept compact for the narrow chat side-panel.
//
// react-markdown v10 passes a `node` prop to every component override; it must be
// destructured OUT before spreading the rest onto the DOM element (otherwise
// React warns about an unknown `node` attribute).
import { useCallback, useState, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { extractCopyText } from '../utils/chatUi'

function CodeBlockPre({ children, ...rest }: { children?: ReactNode } & Record<string, unknown>) {
  const [copied, setCopied] = useState(false)
  const onCopy = useCallback(async () => {
    const text = extractCopyText(children)
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard may be denied; leave UI unchanged.
    }
  }, [children])

  return (
    <div className="relative my-1.5 group">
      <pre className="p-2 pr-14 rounded bg-bg-2 overflow-x-auto text-[12px]" {...rest}>
        {children}
      </pre>
      <button
        type="button"
        className="absolute top-1.5 right-1.5 px-1.5 py-0.5 text-[10px] rounded border border-border bg-bg/90 text-muted hover:text-text opacity-80 group-hover:opacity-100"
        onClick={onCopy}
        data-testid="chat-code-copy"
        aria-label="Copy code"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

const components: Components = {
  h1: ({ node, ...p }) => <h1 className="text-[15px] font-semibold text-text mt-3 mb-1.5 first:mt-0" {...p} />,
  h2: ({ node, ...p }) => <h2 className="text-[14px] font-semibold text-text mt-3 mb-1.5 first:mt-0" {...p} />,
  h3: ({ node, ...p }) => <h3 className="text-[13px] font-semibold text-text mt-2.5 mb-1 first:mt-0" {...p} />,
  h4: ({ node, ...p }) => <h4 className="text-[12.5px] font-semibold text-muted mt-2 mb-1 first:mt-0" {...p} />,
  p:  ({ node, ...p }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0" {...p} />,
  strong: ({ node, ...p }) => <strong className="font-semibold text-text" {...p} />,
  em: ({ node, ...p }) => <em className="italic" {...p} />,
  ul: ({ node, ...p }) => <ul className="my-1.5 ml-4 list-disc space-y-0.5 marker:text-muted" {...p} />,
  ol: ({ node, ...p }) => <ol className="my-1.5 ml-4 list-decimal space-y-0.5 marker:text-muted" {...p} />,
  li: ({ node, ...p }) => <li className="leading-relaxed" {...p} />,
  a:  ({ node, ...p }) => <a className="text-accent underline hover:no-underline" target="_blank" rel="noreferrer" {...p} />,
  hr: ({ node, ...p }) => <hr className="my-2 border-border" {...p} />,
  blockquote: ({ node, ...p }) => <blockquote className="my-1.5 border-l-2 border-border pl-2 text-muted" {...p} />,
  // v10 dropped the `inline` prop — inline code has no `language-*` class, fenced
  // blocks do. Style the two differently (chip vs block) off that signal.
  code: ({ node, className, children, ...rest }) => {
    const isBlock = String(className ?? '').includes('language-')
    return isBlock
      ? <code className={'font-mono text-[12px] ' + (className ?? '')} {...rest}>{children}</code>
      : <code className="px-1 py-0.5 rounded bg-bg-3 text-[12px] font-mono text-text" {...rest}>{children}</code>
  },
  pre: ({ node, ...p }) => <CodeBlockPre {...p} />,
  table: ({ node, ...p }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[12px]" {...p} />
    </div>
  ),
  th: ({ node, ...p }) => <th className="border border-border bg-bg-2 px-2 py-1 text-left font-semibold text-text" {...p} />,
  td: ({ node, ...p }) => <td className="border border-border px-2 py-1 align-top" {...p} />,
}

export default function ChatMarkdown({ children }: { children: string }) {
  return (
    <div className="chat-markdown break-words">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </Markdown>
    </div>
  )
}
