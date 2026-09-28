/**
 * The reports API (WP7a) — raw `fetch` wrappers over
 * `/api/projects/{name}/reports…` (backend `routers/reports.py`).
 *
 * Held to the wire: the URL and method of every call, the CSRF header on
 * every mutation (these bypass the axios interceptor, like `uploads.ts`),
 * and the backend's `{detail: {error_kind, message}}` shape mapped to a
 * typed `ReportsError` the panels can switch on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CSRF_HEADER } from './csrf'
import {
  abortGenerate,
  createEvidenceOnlyReport,
  deleteReport,
  exportReport,
  generateReport,
  getGenerateStatus,
  getReport,
  getReportVersion,
  isReportError,
  listReports,
  regenerateSection,
  reportErrorMessage,
  reportFigureUrl,
  reportJobErrorMessage,
  ReportsError,
  type ReportJobRecord,
  type ReportMeta,
} from './reports'

const META: ReportMeta = {
  report_id: 'a1b2c3d4e5f60718',
  title: 'Study report — Demo',
  created_at: '2026-09-28T10:00:00+00:00',
  updated_at: '2026-09-28T10:00:00+00:00',
  latest_version: 1,
  mode: 'evidence_only',
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  document.cookie = 'pypsa_gui_csrf=tok'
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'pypsa_gui_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
})

function lastCall(): { url: string; init: RequestInit | undefined } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit | undefined]
  return { url, init }
}

describe('reports api — reads', () => {
  it('lists reports of a project (GET, project name encoded)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([META]))
    const out = await listReports('My Project')
    expect(out).toEqual([META])
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/My%20Project/reports')
    expect(init?.method ?? 'GET').toBe('GET')
  })

  it('gets the latest document by default and a version through ?version=', async () => {
    // A fresh Response per call: a body can be read once.
    fetchMock.mockImplementation(async () => jsonResponse({ report_id: META.report_id, version: 2 }))
    await getReport('Demo', META.report_id)
    expect(lastCall().url).toBe(`/api/projects/Demo/reports/${META.report_id}`)
    await getReport('Demo', META.report_id, 2)
    expect(lastCall().url).toBe(`/api/projects/Demo/reports/${META.report_id}?version=2`)
  })

  it('gets one version through the versions route', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ report_id: META.report_id, version: 3 }))
    const doc = await getReportVersion('Demo', META.report_id, 3)
    expect(doc.version).toBe(3)
    expect(lastCall().url).toBe(`/api/projects/Demo/reports/${META.report_id}/versions/3`)
  })

  it('builds the figure URL for an <img src>', () => {
    expect(reportFigureUrl('My Project', META.report_id, 'fmea_pareto'))
      .toBe(`/api/projects/My%20Project/reports/${META.report_id}/figures/fmea_pareto`)
  })
})

describe('reports api — mutations carry the CSRF header', () => {
  it('creates an evidence-only report with POST {mode: "evidence_only"}', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...META, document: { report_id: META.report_id } }))
    const out = await createEvidenceOnlyReport('Demo', 'Client report')
    expect(out.report_id).toBe(META.report_id)
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/Demo/reports')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ mode: 'evidence_only', title: 'Client report' })
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('omits the title when none is given', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...META, document: {} }))
    await createEvidenceOnlyReport('Demo')
    expect(JSON.parse(String(lastCall().init?.body))).toEqual({ mode: 'evidence_only' })
  })

  it('exports a version to .docx and returns the upload meta', async () => {
    const meta = { file_id: 'f1', filename: 'report.docx', kind: 'agent_export' }
    fetchMock.mockResolvedValueOnce(jsonResponse(meta))
    const out = await exportReport('Demo', META.report_id, { version: 2, filename: 'client' })
    expect(out.file_id).toBe('f1')
    const { url, init } = lastCall()
    expect(url).toBe(`/api/projects/Demo/reports/${META.report_id}/export`)
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ version: 2, filename: 'client' })
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
  })

  it('deletes a report with DELETE + CSRF', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ deleted: true, report_id: META.report_id }))
    const out = await deleteReport('Demo', META.report_id)
    expect(out.deleted).toBe(true)
    const { url, init } = lastCall()
    expect(url).toBe(`/api/projects/Demo/reports/${META.report_id}`)
    expect(init?.method).toBe('DELETE')
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
  })
})

describe('reports api — error kinds', () => {
  it('maps report_not_found (404)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { detail: { error_kind: 'report_not_found', message: 'No such report.' } }, 404,
    ))
    const err = await getReport('Demo', META.report_id).catch(e => e)
    expect(err).toBeInstanceOf(ReportsError)
    expect(err.status).toBe(404)
    expect(err.detail.error_kind).toBe('report_not_found')
    expect(isReportError(err, 'report_not_found')).toBe(true)
    expect(isReportError(err, 'invalid_report_id')).toBe(false)
  })

  it('maps invalid_report_id (400)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { detail: { error_kind: 'invalid_report_id', message: 'Report ids are 16 lowercase hex characters.' } }, 400,
    ))
    const err = await deleteReport('Demo', 'nope').catch(e => e)
    expect(isReportError(err, 'invalid_report_id')).toBe(true)
    expect(err.message).toBe('Report ids are 16 lowercase hex characters.')
  })

  it('maps report_mode_not_supported (400)', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { detail: { error_kind: 'report_mode_not_supported', message: 'Use mode evidence_only.' } }, 400,
    ))
    const err = await createEvidenceOnlyReport('Demo').catch(e => e)
    expect(isReportError(err, 'report_mode_not_supported')).toBe(true)
  })

  it('maps the edit-lock refusal (409 project_locked) and keeps its message', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { detail: { error_kind: 'project_locked', message: "'Demo' is being edited by another user.", lock: {} } }, 409,
    ))
    const err = await deleteReport('Demo', META.report_id).catch(e => e)
    expect(isReportError(err, 'project_locked')).toBe(true)
    expect(err.status).toBe(409)
    expect(reportErrorMessage(err)).toContain('being edited by another user')
  })

  it('keeps a non-JSON failure as an unknown kind with the status', async () => {
    fetchMock.mockResolvedValueOnce(new Response('boom', { status: 502, statusText: 'Bad Gateway' }))
    const err = await listReports('Demo').catch(e => e)
    expect(err).toBeInstanceOf(ReportsError)
    expect(err.detail.error_kind).toBe('unknown')
    expect(err.status).toBe(502)
    expect(reportErrorMessage(err)).toContain('502')
  })

  it('reportErrorMessage falls back for a plain Error and isReportError is false for it', () => {
    const e = new Error('network down')
    expect(isReportError(e)).toBe(false)
    expect(reportErrorMessage(e)).toBe('network down')
  })
})

// ── WP7b: the generation job routes (backend `routers/report_jobs.py`) ──────

const RECORD: ReportJobRecord = {
  status: 'running',
  report_id: META.report_id,
  version: null,
  mode: 'generate',
  section: null,
  progress: { done: 1, total: 3, current: 'cost' },
  repairs: 0,
  prose_failures: [],
  error: null,
  started_at: 1_790_000_000,
  finished_at: null,
  profile_id: 'anthropic-default',
  model: 'claude-sonnet',
}

describe('reports api — generation job (WP7b)', () => {
  it('starts a generated report with POST …/generate and the full body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'running', report_id: META.report_id }))
    const out = await generateReport('My Project', {
      title: 'Client report', language: 'de', sections: ['target', 'cost'], instruction: 'Be brief.',
    })
    expect(out).toEqual({ status: 'running', report_id: META.report_id })
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/My%20Project/reports/generate')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      title: 'Client report', language: 'de', sections: ['target', 'cost'], instruction: 'Be brief.',
    })
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('defaults the language to "en" and omits empty optional fields', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'running', report_id: META.report_id }))
    await generateReport('Demo', { title: '  ', sections: [], instruction: '' })
    expect(JSON.parse(String(lastCall().init?.body))).toEqual({ language: 'en' })
  })

  it('reads the job record from GET …/generate/status', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(RECORD))
    const out = await getGenerateStatus('Demo')
    expect(out).toEqual(RECORD)
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/Demo/reports/generate/status')
    expect(init?.method ?? 'GET').toBe('GET')
  })

  it('maps a 204 (never run) status to null', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    expect(await getGenerateStatus('Demo')).toBeNull()
  })

  it('aborts with POST …/generate/abort + CSRF and returns {status, aborting}', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'running', aborting: true }))
    const out = await abortGenerate('Demo')
    expect(out).toEqual({ status: 'running', aborting: true })
    const { url, init } = lastCall()
    expect(url).toBe('/api/projects/Demo/reports/generate/abort')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
  })

  it('regenerates one section with POST …/{id}/sections/{section}/regenerate', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'running', report_id: META.report_id, version: 2 }))
    const out = await regenerateSection('Demo', META.report_id, 'fmea_top', { instruction: 'Shorter.' })
    expect(out).toEqual({ status: 'running', report_id: META.report_id, version: 2 })
    const { url, init } = lastCall()
    expect(url).toBe(`/api/projects/Demo/reports/${META.report_id}/sections/fmea_top/regenerate`)
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ instruction: 'Shorter.' })
    expect((init?.headers as Record<string, string>)[CSRF_HEADER]).toBe('tok')
  })

  it('regenerate sends an empty body when nothing is given and carries a language when set', async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ status: 'running', report_id: META.report_id, version: 1 }))
    await regenerateSection('Demo', META.report_id, 'cost')
    expect(JSON.parse(String(lastCall().init?.body))).toEqual({})
    await regenerateSection('Demo', META.report_id, 'cost', { language: 'fr', instruction: '  ' })
    expect(JSON.parse(String(lastCall().init?.body))).toEqual({ language: 'fr' })
  })

  it('maps the job error kinds and gives each its toast copy', async () => {
    const cases: Array<[string, number, RegExp]> = [
      ['report_job_in_flight', 409, /already being written/i],
      ['no_evidence', 400, /run a study first/i],
      ['missing_api_key', 400, /configure an LLM profile in Settings/i],
      ['sdk_not_installed', 400, /configure an LLM profile in Settings/i],
      ['project_locked', 409, /being edited by another user/i],
      ['report_section_not_found', 404, /no section/i],
    ]
    for (const [kind, status, copy] of cases) {
      fetchMock.mockResolvedValueOnce(jsonResponse(
        { detail: { error_kind: kind, message: `backend says ${kind}: 'Demo' is being edited by another user; no section 'x'.` } }, status,
      ))
      const err = await generateReport('Demo', {}).catch(e => e)
      expect(err).toBeInstanceOf(ReportsError)
      expect(err.status).toBe(status)
      expect(isReportError(err, kind as never)).toBe(true)
      expect(reportJobErrorMessage(err)).toMatch(copy)
    }
    // Unknown kinds fall back to the backend message; a plain Error keeps its text.
    expect(reportJobErrorMessage(new ReportsError({ error_kind: 'weird', message: 'odd' }, 500))).toBe('odd')
    expect(reportJobErrorMessage(new Error('network down'))).toBe('network down')
  })
})
