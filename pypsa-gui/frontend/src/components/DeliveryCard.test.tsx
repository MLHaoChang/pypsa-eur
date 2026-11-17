// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import DeliveryCard, { deliveryFromResult } from './DeliveryCard'

const BLOB = '/api/projects/Demo/uploads/0123456789abcdef/blob'

describe('deliveryFromResult', () => {
  it('reads a delivery with its expiry', () => {
    expect(deliveryFromResult({ download_url: BLOB, filename: 'a.xlsx', expires_at: '2026-11-24T10:00:00+00:00' }))
      .toEqual({ href: BLOB, filename: 'a.xlsx', expiresOn: '2026-11-24', expired: false })
  })
  it('reads an expired delivery refusal', () => {
    expect(deliveryFromResult({ ok: false, error_kind: 'delivery_expired', filename: 'a.xlsx' }))
      .toEqual({ href: null, filename: 'a.xlsx', expiresOn: null, expired: true })
  })
  it('refuses a link outside the project upload blob path', () => {
    expect(deliveryFromResult({ download_url: 'https://evil.example/x', filename: 'a.xlsx' })).toBeNull()
  })
  it('drops an expiry that is not a date', () => {
    expect(deliveryFromResult({ download_url: BLOB, expires_at: 'soon' })?.expiresOn).toBeNull()
  })
})

describe('DeliveryCard', () => {
  afterEach(cleanup)
  it('names the file, links the download and gives the ISO expiry date', () => {
    render(<DeliveryCard delivery={{ href: BLOB, filename: 'a.xlsx', expiresOn: '2026-11-24', expired: false }} />)
    expect(screen.getByTestId('delivery-card').textContent).toContain('File ready: a.xlsx')
    const link = screen.getByRole('link', { name: 'Download' })
    expect(link.getAttribute('href')).toBe(BLOB)
    expect(link.getAttribute('download')).toBe('a.xlsx')
    expect(screen.getByText('Link valid until 2026-11-24')).toBeTruthy()
  })
  it('says an expired link needs a new export and offers no download', () => {
    render(<DeliveryCard delivery={{ href: null, filename: 'a.xlsx', expiresOn: null, expired: true }} />)
    expect(screen.getByTestId('delivery-card').textContent).toContain('Link expired: export again')
    expect(screen.queryByRole('link')).toBeNull()
  })
  it('omits the expiry line when the server gave none', () => {
    render(<DeliveryCard delivery={{ href: BLOB, filename: null, expiresOn: null, expired: false }} />)
    expect(screen.getByTestId('delivery-card').textContent).toContain('File ready: file')
    expect(screen.queryByText(/Link valid until/)).toBeNull()
  })
})
