import { describe, expect, it } from 'vitest'
import { headerSize, readLayoutFile, sniff, LayoutFileRefused, MAX_FILE_BYTES } from './layoutFile'

const enc = (s: string) => Array.from(s, c => c.charCodeAt(0))
const be32 = (n: number) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
const le24 = (n: number) => [n & 255, (n >>> 8) & 255, (n >>> 16) & 255]

const png = (w: number, h: number) => new Uint8Array([0x89, ...enc('PNG\r\n\x1a\n'), 0, 0, 0, 13, ...enc('IHDR'), ...be32(w), ...be32(h), 8, 6, 0, 0, 0])
const jpeg = (w: number, h: number) => new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, ...new Array(14).fill(0),
  0xff, 0xc0, 0, 17, 8, (h >> 8) & 255, h & 255, (w >> 8) & 255, w & 255, 3, 0, 0, 0])
const webp = (w: number, h: number) => new Uint8Array([...enc('RIFF'), 0, 0, 0, 0, ...enc('WEBPVP8X'), 10, 0, 0, 0, 0, 0, 0, 0,
  ...le24(w - 1), ...le24(h - 1)])
const file = (bytes: Uint8Array | string, name = 'x') => new File([bytes], name)

async function refusal(f: File): Promise<string> {
  try { await readLayoutFile(f) } catch (e) { expect(e).toBeInstanceOf(LayoutFileRefused); return (e as Error).message }
  throw new Error('not refused')
}

describe('layout file checks (SC-06-layout, V40)', () => {
  it('reads PNG, JPEG and WebP sizes from the header without decoding', async () => {
    expect(headerSize('image/png', png(1000, 800))).toEqual({ w: 1000, h: 800 })
    expect(headerSize('image/jpeg', jpeg(640, 480))).toEqual({ w: 640, h: 480 })
    expect(headerSize('image/webp', webp(300, 200))).toEqual({ w: 300, h: 200 })
    expect(await readLayoutFile(file(png(1000, 800), 'plan.png'))).toEqual({ kind: 'raster', media_type: 'image/png', width_px: 1000, height_px: 800 })
  })

  it('names the format by its bytes, not its file name', async () => {
    expect(await readLayoutFile(file(png(10, 10), 'plan.geojson'))).toMatchObject({ kind: 'raster' })
    expect(await refusal(file('%PDF-1.7 ...', 'plan.png'))).toMatch(/^PDF files are not accepted/)
  })

  it.each([
    ['%PDF-1.4', 'PDF'], ['<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', 'SVG'],
    ['0\nSECTION\n2\nHEADER', 'DXF'], ['ISO-10303-21;\nHEADER;', 'IFC'], ['AC1032....', 'DWG'], ['PK\x03\x04', 'ZIP archive'],
  ])('refuses %s as %s by name', async (body, name) => {
    expect(sniff(new Uint8Array(enc(body)))).toBe(name)
    const msg = await refusal(file(body))
    expect(msg).toContain(`${name} files are not accepted`)
    expect(msg).toContain('GeoJSON')
  })

  it('refuses an image over 50 megapixels or 20 000 px before decoding', async () => {
    expect(await refusal(file(png(10_000, 6_000)))).toBe('The image is 60.0 megapixels: the limit is 50')
    expect(await refusal(file(png(20_001, 10)))).toBe('The image is 20001 pixels across: the limit is 20000')
    expect(await refusal(file(jpeg(9000, 9000)))).toMatch(/megapixels/)
  })

  it('refuses a file over 25 MB and an unreadable header', async () => {
    const big = new File([new Uint8Array(MAX_FILE_BYTES + 1)], 'big.png')
    expect(await refusal(big)).toMatch(/limit is 25 MB/)
    expect(await refusal(file(png(10, 10).slice(0, 12)))).toMatch(/size could not be read/)
  })

  it('parses GeoJSON and refuses broken or oversized JSON', async () => {
    const doc = { type: 'FeatureCollection', features: [] }
    expect(await readLayoutFile(file(JSON.stringify(doc)))).toEqual({ kind: 'geojson', document: doc })
    expect(await refusal(file('{ not json'))).toBe('The file is not valid GeoJSON')
    expect(await refusal(file(`{"a":"${'x'.repeat(4 * 1024 * 1024)}"}`))).toMatch(/over 4 MB/)
  })
})
