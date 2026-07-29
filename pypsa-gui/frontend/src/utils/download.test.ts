// Downloading a file by URL (phase 2a, Task 6).
//
// Measured against a real cocoa WKWebView (backend/smoke/audit_downloads.py):
//
//   window.open(url, '_blank')   returns null, NOTHING happens, on both
//                                settings of ALLOW_DOWNLOADS — because
//                                createWebViewWithConfiguration acts only on
//                                WKNavigationTypeLinkActivated and a JS
//                                window.open is not that.
//   <a href download>.click()    saves through the native panel, byte-exact,
//                                page intact, 64 MB streamed without
//                                marshalling.
//
// So the bundle export becomes an anchor. The 12 blob exports elsewhere in the
// app already ARE anchors and are deliberately left alone — they were never
// the broken ones.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { saveFromUrl } from './download'

type Click = { href: string; download: string | null; target: string | null; attached: boolean }

let clicks: Click[]

beforeEach(() => {
  clicks = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicks.push({
      href: this.getAttribute('href') ?? '',
      download: this.getAttribute('download'),
      target: this.getAttribute('target'),
      // Safari and WebKit both ignore a click on a detached anchor in some
      // versions; capturing this at click time rather than after is the only
      // way to tell "attached then removed" from "never attached".
      attached: document.body.contains(this),
    })
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('saveFromUrl', () => {
  it('clicks an anchor carrying the URL and the filename', () => {
    saveFromUrl('/api/projects/Demo/bundle', 'Demo.pypsaproj.zip')

    expect(clicks).toHaveLength(1)
    expect(clicks[0].href).toBe('/api/projects/Demo/bundle')
    expect(clicks[0].download).toBe('Demo.pypsaproj.zip')
  })

  it('does not use window.open, which silently does nothing in the desktop shell', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)

    saveFromUrl('/api/projects/Demo/bundle', 'Demo.pypsaproj.zip')

    expect(open).not.toHaveBeenCalled()
  })

  it('sets no target, so the system browser never takes the download', () => {
    // A target=_blank anchor IS WKNavigationTypeLinkActivated, which reaches
    // OPEN_EXTERNAL_LINKS_IN_BROWSER — default TRUE — and pywebview hands the
    // URL to Safari via webbrowser.open. The file would download outside the
    // app, into the real ~/Downloads, with the app showing no sign of it.
    saveFromUrl('/api/projects/Demo/bundle', 'Demo.pypsaproj.zip')

    expect(clicks[0].target).toBeNull()
  })

  it('attaches the anchor before clicking it', () => {
    saveFromUrl('/api/projects/Demo/bundle', 'Demo.pypsaproj.zip')

    expect(clicks[0].attached).toBe(true)
  })

  it('leaves no anchors behind', () => {
    // The bundle can be exported repeatedly. An anchor per export accumulating
    // in the body is a leak that only shows up after a long session.
    saveFromUrl('/a', 'a.zip')
    saveFromUrl('/b', 'b.zip')

    expect(document.querySelectorAll('a[download]')).toHaveLength(0)
  })
})
