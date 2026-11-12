// Draws a serialised chart SVG onto a canvas and returns it as a PNG, for the
// study report (ui-modern 02). The backend refuses a side over 8000 px or
// more than 16 M pixels, so the scale is chosen to stay inside both.
export const PNG_MAX_SIDE = 8000
export const PNG_MAX_PIXELS = 16_000_000

/** The pixel size to draw at: 2x for a sharp picture, shrunk to fit the caps. */
export function pngSize(width: number, height: number, scale = 2): { w: number; h: number } {
  let k = scale
  k = Math.min(k, PNG_MAX_SIDE / Math.max(width, 1), PNG_MAX_SIDE / Math.max(height, 1))
  const pixels = width * height * k * k
  if (pixels > PNG_MAX_PIXELS) k *= Math.sqrt(PNG_MAX_PIXELS / pixels)
  const w = Math.max(1, Math.floor(width * k))
  const h = Math.max(1, Math.floor(height * k))
  return { w, h }
}

export function svgToPngBlob(source: string, width: number, height: number): Promise<Blob> {
  const { w, h } = pngSize(width, height)
  const url = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }))
  return new Promise<Blob>((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = w
        canvas.height = h
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('no canvas')
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, w, h)
        ctx.drawImage(img, 0, 0, w, h)
        canvas.toBlob(b => (b ? resolve(b) : reject(new Error('no picture'))), 'image/png')
      } catch (e) {
        reject(e)
      } finally {
        URL.revokeObjectURL(url)
      }
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('The chart could not be drawn')) }
    img.src = url
  })
}
