// "Download report (.docx)" at Guided density (owner decision A, UX review H3/N9):
// the evidence-only report built from the project's current results, rendered to
// .docx and handed to the browser. Expert's Project → Reports stays the place to
// edit it. A refusal (a locked project, nothing to report) is shown as said.
import { useMutation } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { createEvidenceOnlyReport, exportReport, reportErrorMessage } from '../../api/reports'
import { getUploadBlobUrl } from '../../api/uploads'

export default function DownloadReport({ project }: { project: string }) {
  const build = useMutation({
    mutationFn: async () => {
      const report = await createEvidenceOnlyReport(project)
      const file = await exportReport(project, report.report_id, { format: 'docx' })
      const a = document.createElement('a')
      a.href = getUploadBlobUrl(project, file.file_id)
      a.download = file.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      return file
    },
  })
  return (
    <div className="flex flex-col gap-1" data-testid="hub-download-report">
      <button type="button" data-testid="hub-download-report-button"
        onClick={() => build.mutate()} disabled={build.isPending}
        className="self-start inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-60">
        <Download size={13} /> {build.isPending ? 'Preparing the report…' : 'Download report (.docx)'}
      </button>
      {build.isError && (
        <p className="text-[11.5px] text-danger" role="alert">{reportErrorMessage(build.error, 'The report could not be built.')}</p>
      )}
    </div>
  )
}
