// Guide markdown (spec expert-catalogue §6.2): the same react-markdown + remark-gfm
// setup as the chat (components/ChatMarkdown.tsx), with stricter rules because guide
// text is rendered in the app's own voice:
// - raw HTML is skipped, never rendered;
// - images are dropped (a guide is words; an <img> could fetch from anywhere);
// - a link is a guide link (`guide:<id>`, a button that opens that entry here) or an
//   absolute http(s) URL (a new tab, marked "↗"); any other target is plain text.
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { guideLinkId, guideUrlTransform, isWebUrl } from './guideLink'
import { useGuideStore } from './guideStore'

const linkClass = 'text-text underline underline-offset-2 hover:text-accent'

function GuideLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const id = guideLinkId(href)
  if (id) {
    return (
      <button type="button" className={linkClass}
        onClick={() => { const again = guideLinkId(href); if (again) useGuideStore.getState().openGuide(again) }}>
        {children}
      </button>
    )
  }
  if (isWebUrl(href)) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={linkClass}>
        {children}<span aria-hidden="true"> ↗</span><span className="sr-only"> (opens in a new tab)</span>
      </a>
    )
  }
  return <span>{children}</span>
}

const components: Components = {
  p: ({ node, ...p }) => <p className="my-1 leading-relaxed first:mt-0 last:mb-0" {...p} />,
  strong: ({ node, ...p }) => <strong className="font-semibold text-text" {...p} />,
  em: ({ node, ...p }) => <em className="italic" {...p} />,
  ul: ({ node, ...p }) => <ul className="my-1 ml-4 list-disc space-y-0.5 marker:text-muted" {...p} />,
  ol: ({ node, ...p }) => <ol className="my-1 ml-4 list-decimal space-y-0.5 marker:text-muted" {...p} />,
  li: ({ node, ...p }) => <li className="leading-relaxed" {...p} />,
  h1: ({ node, ...p }) => <p className="mt-2 font-semibold text-text" {...p} />,
  h2: ({ node, ...p }) => <p className="mt-2 font-semibold text-text" {...p} />,
  h3: ({ node, ...p }) => <p className="mt-2 font-semibold text-text" {...p} />,
  code: ({ node, ...p }) => <code className="rounded bg-bg-3 px-1 font-mono" {...p} />,
  a: ({ href, children }) => <GuideLink href={href}>{children}</GuideLink>,
}

export default function GuideMarkdown({ children }: { children: string }) {
  return (
    <div className="guide-markdown break-words">
      <Markdown remarkPlugins={[remarkGfm]} components={components} urlTransform={guideUrlTransform}
        skipHtml disallowedElements={['img']}>
        {children}
      </Markdown>
    </div>
  )
}
