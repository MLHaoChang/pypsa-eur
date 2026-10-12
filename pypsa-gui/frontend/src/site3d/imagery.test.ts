import { describe, it, expect } from 'vitest'
import { tileUrl, tileUrls, MAX_TILES, ESRI_IMAGERY_URL } from './imagery'

describe('tile urls', () => {
  it('substitutes z/x/y in the Esri template, y before x as Esri orders them', () => {
    expect(tileUrl(ESRI_IMAGERY_URL, 17, 65536, 43690))
      .toBe('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/17/43690/65536')
  })

  it('enumerates a range row-major, north-west first', () => {
    const urls = tileUrls({ z: 3, x0: 1, x1: 2, y0: 5, y1: 6 }, '{z}/{x}/{y}')
    expect(urls).toEqual(['3/1/5', '3/2/5', '3/1/6', '3/2/6'])
  })

  it('refuses a range past the ceiling instead of firing hundreds of requests', () => {
    expect(() => tileUrls({ z: 3, x0: 0, x1: 7, y0: 0, y1: 7 }, '{z}/{x}/{y}')).not.toThrow()
    expect(() => tileUrls({ z: 3, x0: 0, x1: 8, y0: 0, y1: 7 }, '{z}/{x}/{y}')).toThrow(String(MAX_TILES))
  })
})
