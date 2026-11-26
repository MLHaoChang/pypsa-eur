import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CheckedEvidenceCard } from './CheckedEvidenceCard'
import { CheckedChart } from './CheckedChart'
import AssistantResultCard from './AssistantResultCard'
import type { AnswerReceipt, CheckedChart as Chart } from '../utils/answerAssurance'

// Assistant-assurance 06 (A5.3, GK G3): a card's string fields are text. Raw or
// entity-encoded HTML in them never becomes an element or an attribute, and an
// off-allowlist link field is never offered.
afterEach(cleanup)

const PAYLOADS = [
  '<img src=x onerror="alert(1)">', '&lt;img src=x onerror=alert(1)&gt;',
  '<a href="javascript:alert(1)">x</a>', '&lt;a href=javascript:alert(1)&gt;x&lt;/a&gt;',
  '<script>alert(1)</script>', '&lt;script&gt;alert(1)&lt;/script&gt;',
]

function noInjectedNode(container: HTMLElement) {
  expect(container.querySelector('img, script, iframe, [onerror], [src]')).toBeNull()
  for (const a of container.querySelectorAll('a')) expect(a.getAttribute('href') ?? '').toMatch(/^\/api\//)
}

describe.each(PAYLOADS)('card string fields stay text: %s', (payload) => {
  it('checked evidence card (limitation, source label, slot metric)', () => {
    const receipt: AnswerReceipt = {
      policy_version: '1', content_sha256: 'a'.repeat(64), evidence_ids: ['evidence-1'],
      checked_block_ids: ['energy'], limitations: [payload],
      reviewer_status: 'not_required', request_complete: false,
      request_scope: { profile: 'generator-cost/1', entity_id: payload, retain: true },
      result_slots: [{ metric: payload, disposition: 'unavailable', reason: 'invalid_or_missing_raw_data' }],
    }
    const { container } = render(<CheckedEvidenceCard answerId={payload} receipt={receipt} sourceLabel={payload} />)
    noInjectedNode(container)
    expect(container.textContent).toContain(payload)
  })

  it('checked chart (filename, series)', () => {
    const chart: Chart = {
      status: 'drawn', file_id: '0123456789abcdef', filename: payload,
      download_url: '/api/projects/p1/uploads/0123456789abcdef/blob', png_sha256: 'a'.repeat(64),
      evidence_id: '11111111-1111-1111-1111-111111111111',
      window: { start: '2026-01-01T00:00:00', end: '2026-01-01T01:00:00', snapshots: 2 },
      series: [{ series: payload, disposition: 'drawn', unit: payload }],
    } as Chart
    const { container } = render(<CheckedChart chart={chart} entity={payload} />)
    noInjectedNode(container)
  })

  it('tool result card (task title, filename)', () => {
    const { container } = render(<AssistantResultCard result={{
      filename: payload, download_url: '/api/projects/p1/uploads/0123456789abcdef/blob',
      _task: { task_id: '0ba037fe-bc46-43d3-a9c2-9b587c9cd50e', title: payload, status: payload, completed_steps: 1, total_steps: 2 },
    }} disabled={false} onRequest={vi.fn()} />)
    noInjectedNode(container)
    expect(container.textContent).toContain(payload)
  })
})

describe('card link fields go through the seam', () => {
  it.each(['javascript:alert(1)', 'https://evil.example/x.png', '//evil.example/api/projects/p1/uploads/0123456789abcdef/blob'])(
    'an off-allowlist download is not offered: %s', (url) => {
      const { container } = render(<AssistantResultCard result={{ filename: 'x', download_url: url }} disabled={false} onRequest={vi.fn()} />)
      expect(container.querySelector('a')).toBeNull()
    })
})
