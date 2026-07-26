import { useState, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { adminApi } from '../../api/admin'
import {
  Btn,
  Field,
  PageBody,
  PageHeader,
  PageSection,
  RowGrid,
  StatCard,
  Tag,
} from '../../components/PageKit'

const EMAIL_STATUS_QUERY_KEY = ['admin', 'email-status']
const INPUT_CLASS_NAME = 'w-full rounded-xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-3 py-2 text-sm text-[var(--brand-ink)] outline-none transition focus:border-[var(--brand-red)] focus:ring-2 focus:ring-[rgba(255,82,82,0.28)]'

export default function EmailSettingsPage() {
  const [testRecipient, setTestRecipient] = useState('')

  const { data: status, isLoading } = useQuery({
    queryKey: EMAIL_STATUS_QUERY_KEY,
    queryFn: adminApi.getEmailStatus,
    staleTime: 10_000,
  })

  const sendTestEmail = useMutation({
    mutationFn: () => adminApi.sendTestEmail(testRecipient.trim() ? { to: testRecipient.trim() } : {}),
    onSuccess: (result) => {
      toast.success(`Test email sent to ${result.to}.`)
      setTestRecipient('')
    },
  })

  function handleSendTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    sendTestEmail.mutate()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        eyebrow="Admin / email settings"
        title="Email settings"
        subtitle="Inspect the current SMTP configuration snapshot and send a live delivery test without leaving the admin console."
      />
      <PageBody>
        <RowGrid cols={4}>
          <StatCard accent eyebrow="Configured" value={status?.configured ? 'Yes' : 'No'} sub="SMTP host/from/port completeness" />
          <StatCard eyebrow="Delivery mode" value={status?.delivery_mode ?? '…'} sub="Live SMTP or test outbox" />
          <StatCard eyebrow="Host" value={status?.smtp_host ?? '…'} sub={`Port ${status?.smtp_port ?? '…'}`} />
          <StatCard eyebrow="Credentials" value={status?.smtp_user_set ? 'Present' : 'Empty'} sub="SMTP username configured" />
        </RowGrid>

        <PageSection title="Configuration snapshot">
          {isLoading || !status ? (
            <div className="rounded-2xl border border-dashed border-[var(--brand-line)] bg-[var(--brand-surface)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
              Loading email status…
            </div>
          ) : (
            <dl className="grid gap-3 md:grid-cols-2">
              <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-4 py-3">
                <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--brand-ink-dim)]">Delivery mode</dt>
                <dd className="mt-1 text-sm text-[var(--brand-ink)]">{status.delivery_mode}</dd>
              </div>
              <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-4 py-3">
                <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--brand-ink-dim)]">From address</dt>
                <dd className="mt-1 text-sm text-[var(--brand-ink)]">{status.smtp_from}</dd>
              </div>
              <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-4 py-3">
                <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--brand-ink-dim)]">Host</dt>
                <dd className="mt-1 text-sm text-[var(--brand-ink)]">{status.smtp_host}:{status.smtp_port}</dd>
              </div>
              <div className="rounded-2xl border border-[var(--brand-line)] bg-[var(--brand-surface-2)] px-4 py-3">
                <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--brand-ink-dim)]">Configured</dt>
                <dd className="mt-1">
                  <Tag tone={status.configured ? 'ok' : 'warn'}>
                    {status.configured ? 'ready' : 'incomplete'}
                  </Tag>
                </dd>
              </div>
            </dl>
          )}
        </PageSection>

        <PageSection title="Send test email" hint="Leave the recipient blank to send the test to the current signed-in admin.">
          <form className="flex flex-col gap-4 md:flex-row md:items-end" onSubmit={handleSendTest}>
            <Field label="Recipient override">
              <input
                className={INPUT_CLASS_NAME}
                onChange={event => setTestRecipient(event.target.value)}
                placeholder="deliver@example.com"
                type="email"
                value={testRecipient}
              />
            </Field>
            <Btn disabled={sendTestEmail.isPending} type="submit" variant="primary">
              {sendTestEmail.isPending ? 'Sending…' : 'Send test email'}
            </Btn>
          </form>
        </PageSection>
      </PageBody>
    </div>
  )
}
