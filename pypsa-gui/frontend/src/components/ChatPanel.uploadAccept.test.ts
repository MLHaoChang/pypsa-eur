// The picker offers every file type the upload endpoint takes (#778 added GeoJSON and KML).
import { expect, it } from 'vitest'
import { UPLOAD_ACCEPT } from './ChatPanel'

it('lets the user pick GeoJSON and KML map files', () => {
  const kinds = UPLOAD_ACCEPT.split(',')
  expect(kinds).toContain('.geojson')
  expect(kinds).toContain('.kml')
})
