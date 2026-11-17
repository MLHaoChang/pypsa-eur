// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * Live-voice 03b: the console's card for a file the assistant made.
 *
 * Reads a delivery tool result (`get_file_delivery`, `build_delivery`). The link
 * is accepted only where `downloadHref` accepts it (the project's own upload
 * blob path). A 410 `delivery_expired` refusal (LC48A-TTL-1, 7 days) shows
 * that the user has to export again; it never offers a dead link.
 */
import { downloadHref } from './CompanionOutcome'

export interface Delivery {
  href: string | null
  filename: string | null
  /** ISO date (YYYY-MM-DD) the link stops working, when the server gave one. */
  expiresOn: string | null
  expired: boolean
}

const ISO_DATE = /^(\d{4}-\d{2}-\d{2})T/

export function deliveryFromResult(result: unknown): Delivery | null {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return null
  const record = result as { error_kind?: unknown; filename?: unknown; expires_at?: unknown }
  const filename = typeof record.filename === 'string' && record.filename.length <= 180 ? record.filename : null
  if (record.error_kind === 'delivery_expired') return { href: null, filename, expiresOn: null, expired: true }
  const link = downloadHref(result)
  if (!link) return null
  const match = typeof record.expires_at === 'string' ? ISO_DATE.exec(record.expires_at) : null
  return { href: link.href, filename: link.filename, expiresOn: match ? match[1] : null, expired: false }
}

export default function DeliveryCard({ delivery }: { delivery: Delivery }) {
  const name = delivery.filename ?? 'file'
  return (
    <div className="rounded border border-border bg-bg-2 px-2 py-1.5 text-[11px]" data-testid="delivery-card">
      {delivery.expired || !delivery.href ? (
        <div className="text-warn">Link expired: export again <span className="text-muted">· {name}</span></div>
      ) : (
        <>
          <div>
            <span className="font-semibold">File ready: {name}</span>
            {' · '}
            <a className="font-semibold underline" href={delivery.href} download={name}>Download</a>
          </div>
          {delivery.expiresOn && <div className="text-muted">Link valid until {delivery.expiresOn}</div>}
        </>
      )}
    </div>
  )
}
