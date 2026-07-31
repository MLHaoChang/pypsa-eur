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
