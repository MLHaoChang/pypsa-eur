import { vi } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import client from '../api/client'
import { nk } from '../utils/queryKeys'

/** The rows each `GET /network/<resource>` answers in the running test. */
const served: Record<string, unknown[]> = {}

/**
 * Put a class's rows in the query cache AND make the network read answer the same rows. `updateAsset` rebases a save
 * on a fresh read of the class (05g), so a test that seeds only the cache would have that read fail. The same rows
 * mean "nothing changed since the cache was filled". Every other request goes through untouched.
 */
export function seedRows(qc: QueryClient, project: string | null, root: string, rows: unknown[]) {
  qc.setQueryData(nk(project, root), rows)
  served[`/network/${root}`] = rows
  if (vi.isMockFunction(client.get)) return
  const original = client.get.bind(client)
  vi.spyOn(client, 'get').mockImplementation(((url: string, ...rest: unknown[]) =>
    url in served ? Promise.resolve({ data: served[url] })
      : (original as (u: string, ...r: unknown[]) => Promise<unknown>)(url, ...rest)) as never)
}

/** The rows seeded for a class, for a test whose `networkApi` mock answers the class list itself. */
export const servedRows = (root: string): unknown[] => served[`/network/${root}`] ?? []
