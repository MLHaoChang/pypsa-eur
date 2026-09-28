// One section of a `ReportDocument` (WP7a).
//
// Renders what the store holds and nothing else: prose blocks through the
// same ChatMarkdown the assistant uses, `table_ref` / `figure_ref` resolved
// against the document's OWN `tables` / `figures` maps (rendered by backend
// code from the evidence — the model never wrote a cell), callouts as
// labelled boxes with an icon and a word (never colour alone), and a section
// whose status is not `ok` STATING its note rather than disappearing (EH
// spec §4 completeness enum; ADR-0001 for the "not established" rule).
//
// Numbers the audit listed in `audit.unverified` are wrapped in a `<mark>`
// inside the rendered prose (`highlightUnverified.ts`), with the tooltip
// "not found in the evidence".
import { useMemo } from 'react'
import { AlertTriangle, CircleSlash, Info } from 'lucide-react'
import type { Options } from 'react-markdown'
import ChatMarkdown from '../../components/ChatMarkdown'
import { Tag } from '../../components/PageKit'
import {
  reportFigureUrl,
  type Block,
  type CalloutKind,
  type ReportDocument,
  type Section,
  type SectionSource,
  type SectionStatus,
} from '../../api/reports'
import { rehypeMarkUnverified } from './highlightUnverified'

const STATUS_LABEL: Record<SectionStatus, string> = {
  ok: 'ok',
  not_established: 'not established',
  skipped: 'skipped',
}
const STATUS_TONE: Record<SectionStatus, 'ok' | 'warn' | 'neutral'> = {
  ok: 'ok',
  not_established: 'warn',
  skipped: 'neutral',
}

const SOURCE_LABEL: Record<SectionSource, string> = {
  llm: 'model prose',
  code: 'from evidence',
  user_edit: 'edited',
}

const CALLOUT: Record<CalloutKind, { label: string; icon: React.ReactNode; className: string }> = {
  disclosure: {
    label: 'Disclosure',
    icon: <Info size={13} aria-hidden="true" />,
    className: 'border-accent/40 bg-accent-50/40',
  },
  gap: {
    label: 'Evidence gap',
    icon: <AlertTriangle size={13} aria-hidden="true" />,
    className: 'border-warn/40 bg-warn/10',
  },
  not_established: {
    label: 'Not established',
    icon: <CircleSlash size={13} aria-hidden="true" />,
    className: 'border-border-2 bg-bg-2',
  },
}

function bulletsMarkdown(items: string[]): string {
  return items.map(i => `- ${i}`).join('\n')
}

function BlockView({
  block, doc, project, plugins, index,
}: {
  block: Block
  doc: ReportDocument
  project: string
  plugins: Options['rehypePlugins']
  index: number
}) {
  switch (block.type) {
    case 'paragraph':
      return (
        <div className="text-[12.5px] text-text" data-testid="block-paragraph">
          <ChatMarkdown rehypePlugins={plugins}>{block.md}</ChatMarkdown>
        </div>
      )
    case 'bullets':
      return (
        <div className="text-[12.5px] text-text" data-testid="block-bullets">
          <ChatMarkdown rehypePlugins={plugins}>{bulletsMarkdown(block.items)}</ChatMarkdown>
        </div>
      )
    case 'table_ref': {
      const table = doc.tables[block.table_id]
      if (!table) {
        return (
          <p className="text-[11px] text-muted italic" data-testid="table-missing">
            Table "{block.table_id}" is not in this report.
          </p>
        )
      }
      const caption = block.caption ?? table.caption
      return (
        <div className="overflow-x-auto" data-testid="block-table">
          <table className="w-full border-collapse text-[11.5px]">
            {caption && (
              <caption className="text-left text-[11px] text-muted pb-1 caption-top">{caption}</caption>
            )}
            <thead className="text-muted text-left">
              <tr>
                {table.columns.map((c, i) => (
                  <th key={i} className="border border-border bg-bg-2 px-2 py-1 font-semibold text-text">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border border-border px-2 py-1 align-top font-mono">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {table.source_path && (
            <p className="text-[10px] text-muted font-mono mt-0.5">source: {table.source_path}</p>
          )}
        </div>
      )
    }
    case 'figure_ref': {
      const figure = doc.figures[block.figure_id]
      if (!figure) {
        return (
          <p className="text-[11px] text-muted italic" data-testid="figure-missing">
            Figure "{block.figure_id}" was not produced for this report.
          </p>
        )
      }
      const caption = block.caption ?? figure.caption ?? figure.figure_id
      return (
        <figure className="my-1" data-testid="block-figure">
          <img
            src={reportFigureUrl(project, doc.report_id, figure.figure_id)}
            alt={caption}
            className="max-w-full border border-border rounded bg-white"
          />
          <figcaption className="text-[11px] text-muted mt-1">{caption}</figcaption>
        </figure>
      )
    }
    case 'callout': {
      const c = CALLOUT[block.kind]
      return (
        <div
          className={`flex items-start gap-2 border rounded px-2.5 py-2 text-[12px] ${c.className}`}
          data-testid={`callout-${block.kind}`}
          role="note"
        >
          <span className="shrink-0 mt-0.5 text-muted">{c.icon}</span>
          <span>
            <span className="font-semibold text-text mr-1.5">{c.label}:</span>
            <span className="text-text">{block.text}</span>
          </span>
        </div>
      )
    }
    case 'field':
      return (
        <dl className="flex gap-3 text-[12px]" data-testid="block-field">
          <dt className="text-muted min-w-[140px]">{block.key}</dt>
          <dd className="text-text font-mono">{block.value}</dd>
        </dl>
      )
    default:
      return (
        <p className="text-[11px] text-muted italic" data-testid={`block-unknown-${index}`}>
          Unsupported block.
        </p>
      )
  }
}

export function SectionCard({
  section, doc, project,
}: {
  section: Section
  doc: ReportDocument
  project: string
}) {
  const unverified = section.audit?.unverified ?? []
  const plugins = useMemo<Options['rehypePlugins']>(
    () => (unverified.length ? [[rehypeMarkUnverified, unverified]] : undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [unverified.join('\u0000')],
  )
  return (
    <section
      className="rounded-[10px] border border-border bg-bg overflow-hidden"
      data-testid="report-section"
      data-section-id={section.section_id}
    >
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-bg-2">
        <h3 className="text-[12.5px] font-semibold text-text">{section.heading}</h3>
        <span data-testid="section-status">
          <Tag tone={STATUS_TONE[section.status] ?? 'neutral'}>{STATUS_LABEL[section.status] ?? section.status}</Tag>
        </span>
        <span className="text-[10.5px] text-muted">{SOURCE_LABEL[section.source] ?? section.source}</span>
        {unverified.length > 0 && (
          <span className="text-[10.5px] text-warn ml-auto" data-testid="section-unverified-count">
            {unverified.length} number{unverified.length === 1 ? '' : 's'} not found in the evidence
          </span>
        )}
      </header>
      <div className="p-4 flex flex-col gap-2.5">
        {section.status !== 'ok' && section.note && (
          <p className="text-[12px] text-muted italic" data-testid="section-note">{section.note}</p>
        )}
        {section.blocks.map((block, i) => (
          <BlockView key={i} block={block} doc={doc} project={project} plugins={plugins} index={i} />
        ))}
      </div>
    </section>
  )
}
