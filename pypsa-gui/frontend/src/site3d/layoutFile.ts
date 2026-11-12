// SC-06-layout: reads a layout file IN THE BROWSER before anything is sent.
// A drawing (PNG/JPEG/WebP) never leaves the browser: only its declared size
// goes to the server, with the calibration and the traced outlines. Its size
// is read from the file header, so an oversized image is refused before it
// is decoded. GeoJSON is parsed here and sent as the layout document; the
// server validates it again (`services/site_scene/layout.py`). CAD, PDF, SVG
// and archives are refused by name: they are not silently half-read.

export const MAX_FILE_BYTES = 25 * 1024 * 1024          // D05 per-file quota
export const MAX_GEOJSON_BYTES = 4 * 1024 * 1024 - 1024 // fits the 4 MiB proposal request
export const MAX_DECODED_PIXELS = 50_000_000            // D05: 50 MP
export const MAX_IMAGE_SIDE_PX = 20_000

export type RasterType = 'image/png' | 'image/jpeg' | 'image/webp'
export type LayoutFile =
  | { kind: 'geojson'; document: Record<string, unknown> }
  | { kind: 'raster'; media_type: RasterType; width_px: number; height_px: number }

export class LayoutFileRefused extends Error {}

const refuse = (msg: string): never => { throw new LayoutFileRefused(msg) }

const NOT_ACCEPTED = 'export the layout as GeoJSON, or as a PNG, JPEG or WebP image and trace it'

function ascii(b: Uint8Array, from: number, to: number): string {
  return String.fromCharCode(...b.subarray(from, Math.min(to, b.length)))
}

/** The format named by the file's first bytes, never by its name or reported type. */
export function sniff(head: Uint8Array): RasterType | 'json' | string {
  if (head[0] === 0x89 && ascii(head, 1, 4) === 'PNG') return 'image/png'
  if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return 'image/jpeg'
  if (ascii(head, 0, 4) === 'RIFF' && ascii(head, 8, 12) === 'WEBP') return 'image/webp'
  if (ascii(head, 0, 5) === '%PDF-') return 'PDF'
  if (ascii(head, 0, 2) === 'PK') return 'ZIP archive'
  if (/^AC10\d\d$/.test(ascii(head, 0, 6))) return 'DWG'
  const text = ascii(head, 0, 512).replace(/^﻿/, '').trimStart()
  if (text.startsWith('ISO-10303-21')) return 'IFC'
  if (/^0\s*\r?\n\s*SECTION/.test(text)) return 'DXF'
  if (text.startsWith('<')) return /<svg/i.test(text) ? 'SVG' : 'XML'
  if (text.startsWith('{')) return 'json'
  return 'unknown'
}

function be32(b: Uint8Array, i: number) { return ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0 }
function be16(b: Uint8Array, i: number) { return (b[i] << 8) | b[i + 1] }
function le16(b: Uint8Array, i: number) { return b[i] | (b[i + 1] << 8) }
function le24(b: Uint8Array, i: number) { return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) }

/** Width and height from the image header, or null when the header is not readable. */
export function headerSize(type: RasterType, b: Uint8Array): { w: number; h: number } | null {
  if (type === 'image/png') {
    return b.length >= 24 && ascii(b, 12, 16) === 'IHDR' ? { w: be32(b, 16), h: be32(b, 20) } : null
  }
  if (type === 'image/webp') {
    const chunk = ascii(b, 12, 16)
    if (chunk === 'VP8X' && b.length >= 30) return { w: le24(b, 24) + 1, h: le24(b, 27) + 1 }
    if (chunk === 'VP8 ' && b.length >= 30) return { w: le16(b, 26) & 0x3fff, h: le16(b, 28) & 0x3fff }
    if (chunk === 'VP8L' && b.length >= 25) {
      const bits = b[21] | (b[22] << 8) | (b[23] << 16) | (b[24] << 24)
      return { w: (bits & 0x3fff) + 1, h: ((bits >>> 14) & 0x3fff) + 1 }
    }
    return null
  }
  // JPEG: walk the segments to the first start-of-frame.
  let i = 2
  while (i + 9 < b.length) {
    if (b[i] !== 0xff) return null
    const marker = b[i + 1]
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return { w: be16(b, i + 7), h: be16(b, i + 5) }
    }
    i += 2 + be16(b, i + 2)
  }
  return null
}

async function bytes(file: Blob, n?: number): Promise<Uint8Array> {
  const part = n === undefined ? file : file.slice(0, n)
  return new Uint8Array(await part.arrayBuffer())
}

/** Reads and checks one chosen file; throws LayoutFileRefused with words the user can act on. */
export async function readLayoutFile(file: File): Promise<LayoutFile> {
  if (file.size > MAX_FILE_BYTES) refuse(`The file is ${(file.size / 1e6).toFixed(1)} MB: the limit is 25 MB`)
  const kind = sniff(await bytes(file, 512))
  if (kind === 'json') {
    if (file.size > MAX_GEOJSON_BYTES) refuse('The GeoJSON is over 4 MB: simplify it or split it')
    let document: unknown
    try { document = JSON.parse(new TextDecoder().decode(await bytes(file))) } catch { refuse('The file is not valid GeoJSON') }
    if (!document || typeof document !== 'object' || Array.isArray(document)) refuse('The file is not valid GeoJSON')
    return { kind: 'geojson', document: document as Record<string, unknown> }
  }
  if (kind === 'image/png' || kind === 'image/jpeg' || kind === 'image/webp') {
    // A JPEG's frame header may sit after large metadata: read up to 1 MB of header only.
    const size = headerSize(kind, await bytes(file, kind === 'image/jpeg' ? 1 << 20 : 64))
    if (!size || size.w < 1 || size.h < 1) return refuse('The image size could not be read: save it again as PNG')
    if (Math.max(size.w, size.h) > MAX_IMAGE_SIDE_PX) refuse(`The image is ${Math.max(size.w, size.h)} pixels across: the limit is ${MAX_IMAGE_SIDE_PX}`)
    if (size.w * size.h > MAX_DECODED_PIXELS) refuse(`The image is ${(size.w * size.h / 1e6).toFixed(1)} megapixels: the limit is 50`)
    return { kind: 'raster', media_type: kind, width_px: size.w, height_px: size.h }
  }
  return refuse(kind === 'unknown' ? `This file type is not accepted: ${NOT_ACCEPTED}` : `${kind} files are not accepted: ${NOT_ACCEPTED}`)
}
