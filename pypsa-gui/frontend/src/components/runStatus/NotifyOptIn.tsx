import { create } from 'zustand'
import { useUIStore } from '../../store/uiStore'
import { notifyPermission } from '../../utils/runNotify'

/**
 * The browser's notification permission as the app last saw it, shared by
 * the run strip's link and the PREFERENCES toggle so both always agree
 * (UX 2 GO 1). Null where the browser has no Notification API.
 */
export const useNotifyPermission = create<{ permission: NotificationPermission | null }>(() => ({
  permission: notifyPermission(),
}))

/**
 * Ask the browser, only ever from a click (spec §3: never on load), and switch
 * the setting on when it says yes. A "no" leaves the setting off; the footer
 * then says it is blocked.
 */
export async function askToNotify(): Promise<void> {
  if (typeof Notification === 'undefined') return
  let p: NotificationPermission
  try { p = await Notification.requestPermission() } catch { return }
  useNotifyPermission.setState({ permission: p })
  useUIStore.getState().setBrowserNotify(p === 'granted')
}

export const PRIVACY_OFF = 'Shows the project name in the notification'
export const PRIVACY_ON = 'Shows the project name and whether the run finished or failed'
export const BLOCKED = "Blocked in this browser: allow notifications in the browser's site settings"

/**
 * "Notify me when it finishes" on the running line. It stays until the user
 * has chosen either way in the browser's prompt; after that the PREFERENCES
 * toggle is the one place to change it (UX 2 GO 1, 2: never a dead link).
 */
export function NotifyOptIn() {
  const permission = useNotifyPermission(s => s.permission)
  if (permission !== 'default') return null
  return (
    <span data-testid="run-notify-optin" className="hidden sm:flex shrink-0 items-center gap-1 whitespace-nowrap">
      <button type="button" onClick={() => void askToNotify()} className="underline underline-offset-2 font-semibold">
        Notify me when it finishes
      </button>
      <span className="hidden lg:inline opacity-80">· {PRIVACY_OFF}</span>
    </span>
  )
}

/** The PREFERENCES footer's "Browser notifications" setting (UX 2 GO 1, 2). */
export function BrowserNotifyPref() {
  const permission = useNotifyPermission(s => s.permission)
  const on = useUIStore(s => s.browserNotify)
  const setOn = useUIStore(s => s.setBrowserNotify)
  if (permission === null) return null
  const blocked = permission === 'denied'
  const enabled = on && !blocked
  const toggle = () => {
    if (enabled) setOn(false)
    else if (permission === 'granted') setOn(true)
    else void askToNotify()
  }
  return (
    <div data-testid="browser-notify-pref" className="mt-1 px-1">
      <button type="button" aria-pressed={enabled} disabled={blocked} onClick={toggle}
        className="flex w-full items-center justify-between gap-1.5 rounded px-1.5 py-1.5 text-[10px] text-muted disabled:opacity-60"
        style={{ border: '1px solid var(--color-border)' }}>
        <span>Browser notifications</span>
        <span className="font-semibold">{enabled ? 'On' : 'Off'}</span>
      </button>
      <p data-testid="browser-notify-note" className="px-1 pt-0.5 text-[9px] text-ink-400">
        {blocked ? BLOCKED : enabled ? PRIVACY_ON : PRIVACY_OFF}
      </p>
    </div>
  )
}
