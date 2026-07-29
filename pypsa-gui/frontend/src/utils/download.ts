// Saving a file the backend serves at a URL.
//
// This exists for exactly one reason, and it is a desktop-shell reason:
// `window.open(url, '_blank')` silently does nothing inside pywebview. Measured
// on a real cocoa WKWebView (`backend/smoke/audit_downloads.py`) — it returns
// null and no download starts, on BOTH settings of ALLOW_DOWNLOADS, because
// `webView_createWebViewWithConfiguration_...` acts only on
// WKNavigationTypeLinkActivated and a JS `window.open` is not that.
//
// An anchor carrying `download` works: the native save panel opens, the bytes
// are correct, the page survives, and 64 MB streams without any JS->Python
// marshalling. It is also exactly what the browser deployment already does
// everywhere else, so this is not a desktop-only code path — there is nothing
// to feature-detect and no fallback to maintain.
//
// The 12 `URL.createObjectURL` exports elsewhere in the app are deliberately
// NOT routed through here. They are already anchors and already work; changing
// them would be risk with no measured benefit.

/**
 * Save a URL to a file the user chooses.
 *
 * `filename` is a suggestion — every platform's save panel lets the user
 * change it, and the backend's `Content-Disposition` can override it.
 */
export function saveFromUrl(url: string, filename: string): void {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  // NO `target`. A target=_blank anchor is WKNavigationTypeLinkActivated,
  // which reaches pywebview's OPEN_EXTERNAL_LINKS_IN_BROWSER — default TRUE —
  // and hands the URL to the system browser. The file would land in the real
  // ~/Downloads with nothing in the app indicating where it went.
  document.body.appendChild(a)
  try {
    a.click()
  } finally {
    // Removed even if click() throws: the bundle can be exported repeatedly,
    // and an anchor per export accumulating in the body is a leak that only
    // becomes visible in a long session.
    a.remove()
  }
}
