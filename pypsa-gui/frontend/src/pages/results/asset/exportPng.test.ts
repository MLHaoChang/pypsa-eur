import { beforeEach, describe, expect, it, vi } from 'vitest'
import { downloadPNG } from './exportPng'

function mountSvg(): HTMLElement {
  const host = document.createElement('div')
  host.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"></svg>'
  document.body.appendChild(host)
  return host
}

// A 1×1 PNG. Magic bytes 89 50 4e 47 are what the assertion checks for.
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

beforeEach(() => {
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} })
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
  HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
    cb(new Blob([PNG_BYTES], { type: 'image/png' }))
  } as never
  // jsdom never fires Image.onload; resolve it synchronously.
  // `globalThis` (not `global`) — @types/node isn't a dependency here, and
  // globalThis is the standards-based equivalent already covered by the
  // ES2020 lib in tsconfig.json.
  Object.defineProperty(globalThis.Image.prototype, 'src', {
    configurable: true,
    set(this: HTMLImageElement) { setTimeout(() => this.onload?.(new Event('load')), 0) },
  })
})

describe('downloadPNG', () => {
  it('returns false when the container is null', async () => {
    expect(await downloadPNG(null, 'x.png')).toBe(false)
  })

  it('returns false when the container holds no svg', async () => {
    expect(await downloadPNG(document.createElement('div'), 'x.png')).toBe(false)
  })

  it('produces a PNG blob with the right magic bytes', async () => {
    let captured: Blob | null = null
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: (b: Blob) => { captured = b; return 'blob:x' },
      revokeObjectURL: () => {},
    })
    expect(await downloadPNG(mountSvg(), 'chart.png')).toBe(true)
    const bytes = new Uint8Array(await captured!.arrayBuffer())
    expect([...bytes.slice(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47])
  })

  it('scales the canvas so the file is not a blurry screenshot', async () => {
    // vitest.setup.ts stubs Element.prototype.getBoundingClientRect globally
    // (non-zero, fixed 500×500) so recharts' ResponsiveContainer renders in
    // jsdom — see that file's comment. exportPng.ts's width/height fallback
    // chain (`rect.width || svg.clientWidth || attrWidth || 640`) is written
    // assuming jsdom's true default of an all-zero rect, so it falls through
    // to this SVG's width="100"/height="50" attributes. Override the rect
    // back to zero for just this test, restoring that assumption — same
    // per-file override pattern as AssetPicker.test.tsx/AssetTable.test.tsx.
    // Overriding `Element.prototype` (not `HTMLElement.prototype`) because
    // `svg` here is an SVGSVGElement — SVGElement's prototype chain runs
    // through Element, not HTMLElement, so an HTMLElement-level override
    // (as those two files use, for plain HTML container divs) would silently
    // not apply to it.
    Object.defineProperty(Element.prototype, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) }),
    })
    const created: HTMLCanvasElement[] = []
    const realCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag)
      if (tag === 'canvas') created.push(el as HTMLCanvasElement)
      return el
    })
    await downloadPNG(mountSvg(), 'chart.png', 2)
    expect(created[0].width).toBe(200)
    expect(created[0].height).toBe(100)
  })
})
