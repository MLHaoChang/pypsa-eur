// Ground texture for the 3D site view: the Esri World Imagery tiles the map
// view already draws, stitched into one canvas for the site's tile range.
//
// The URL and attribution are the map view's (pages/MapCanvas.tsx). They are
// repeated here rather than imported because importing MapCanvas would pull
// Leaflet into the 3D view's lazy chunk, which is the one thing that chunk
// exists to avoid. If the map's source changes, change this too.
//
// Licensing (assessment §7): these tiles may be displayed with attribution
// but NOT persisted on the free tier. The stitched canvas lives in memory for
// the life of the scene and is never written anywhere.

import { tileCount, type TileRange } from './geo'

export const ESRI_IMAGERY_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
export const ESRI_ATTRIBUTION =
  'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'

export const TILE_PX = 256
/** Never fetch more than this many tiles for one ground plane (an 8×8 mosaic). */
export const MAX_TILES = 64

export function tileUrl(template: string, z: number, x: number, y: number): string {
  return template.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y))
}

/** The URLs a range needs, row-major, or an error when the range is too large. */
export function tileUrls(r: TileRange, template = ESRI_IMAGERY_URL): string[] {
  if (tileCount(r) > MAX_TILES) {
    throw new Error(`tile range is ${tileCount(r)} tiles; the ceiling is ${MAX_TILES}`)
  }
  const out: string[] = []
  for (let y = r.y0; y <= r.y1; y++) for (let x = r.x0; x <= r.x1; x++) out.push(tileUrl(template, r.z, x, y))
  return out
}

function loadImageOnce(url: string): Promise<HTMLImageElement | null> {
  return new Promise(resolve => {
    const img = new Image()
    // Required for the canvas to stay un-tainted so three.js can upload it.
    // ArcGIS Online tile services send `Access-Control-Allow-Origin: *`.
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = url
  })
}

/**
 * One retry, then give up: a missing tile (ocean at z19, a transient 5xx)
 * leaves a grey square rather than failing the whole mosaic.
 */
async function loadImage(url: string): Promise<HTMLImageElement | null> {
  return (await loadImageOnce(url)) ?? loadImageOnce(url)
}

export interface GroundMosaic {
  canvas: HTMLCanvasElement
  /** Tiles that failed to load, for the status line. */
  missing: number
}

/**
 * Fetch every tile in `r` and draw them into one canvas, north-west tile at
 * the top-left. Resolves even when some tiles fail; rejects only when the
 * range exceeds `MAX_TILES`.
 */
export async function buildGroundMosaic(r: TileRange, template = ESRI_IMAGERY_URL): Promise<GroundMosaic> {
  const urls = tileUrls(r, template)
  const cols = r.x1 - r.x0 + 1
  const rows = r.y1 - r.y0 + 1
  const canvas = document.createElement('canvas')
  canvas.width = cols * TILE_PX
  canvas.height = rows * TILE_PX
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('2D canvas unavailable')
  ctx.fillStyle = '#9ca3af'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  const images = await Promise.all(urls.map(loadImage))
  let missing = 0
  images.forEach((img, i) => {
    if (!img) { missing++; return }
    const c = i % cols, rr = Math.floor(i / cols)
    ctx.drawImage(img, c * TILE_PX, rr * TILE_PX, TILE_PX, TILE_PX)
  })
  return { canvas, missing }
}
