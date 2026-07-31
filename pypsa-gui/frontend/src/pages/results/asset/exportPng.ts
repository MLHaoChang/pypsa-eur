/**
 * Rasterise the chart's SVG to a PNG.
 *
 * Recharts styles with presentation attributes rather than CSS, so serialising
 * the SVG and drawing it into a canvas produces a faithful image without
 * inlining computed styles. Charts contain no external images, so the canvas
 * is never tainted and toBlob always succeeds.
 *
 * Deliberately mirrors downloadSVG in ../shared.tsx — same white background
 * rect, same namespace repair, same "return false if the chart has not
 * mounted" contract.
 */
export async function downloadPNG(
  container: HTMLElement | null,
  filename: string,
  scale = 2,
): Promise<boolean> {
  if (!container) return false
  const svg = container.querySelector('svg')
  if (!svg) return false

  // getBoundingClientRect/clientWidth are the primary source of truth (they
  // reflect actual layout, including any CSS scaling) but read as 0 in jsdom,
  // which has no layout engine. Recharts always sets explicit numeric
  // width/height attributes on its root <svg> to match the measured
  // container, so falling back to those before the hardcoded default keeps
  // the export correctly sized even when rect/clientWidth are unavailable.
  const rect = svg.getBoundingClientRect()
  const attrWidth = parseFloat(svg.getAttribute('width') ?? '')
  const attrHeight = parseFloat(svg.getAttribute('height') ?? '')
  const width = Math.max(1, Math.round(rect.width || svg.clientWidth || attrWidth || 640))
  const height = Math.max(1, Math.round(rect.height || svg.clientHeight || attrHeight || 320))

  const clone = svg.cloneNode(true) as SVGSVGElement
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  if (!clone.querySelector('rect[data-bg]')) {
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    bg.setAttribute('width', '100%')
    bg.setAttribute('height', '100%')
    bg.setAttribute('fill', '#ffffff')
    bg.setAttribute('data-bg', 'true')
    clone.insertBefore(bg, clone.firstChild)
  }

  const source = new XMLSerializer().serializeToString(clone)
  const svgUrl = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }))

  try {
    const img = new Image()
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('svg failed to decode'))
      img.src = svgUrl
    })
    const canvas = document.createElement('canvas')
    canvas.width = width * scale
    canvas.height = height * scale
    const ctx = canvas.getContext('2d')
    if (!ctx) return false
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

    const blob = await new Promise<Blob | null>(res => canvas.toBlob(res, 'image/png'))
    if (!blob) return false
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
    return true
  } catch {
    return false
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}
