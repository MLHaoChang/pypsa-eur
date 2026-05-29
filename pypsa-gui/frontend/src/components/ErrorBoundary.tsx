import React from 'react'

/**
 * Reusable render-error boundary. Catches a crash in its subtree and shows a
 * compact inline fallback (with a Retry that clears the caught error) instead
 * of letting the error bubble to the app-level boundary and whitescreen
 * everything else.
 *
 * Reset semantics: state clears on the Retry button OR when the element is
 * remounted via a changed React `key` (callers key it on the navigation
 * context — e.g. active panel + current project — so navigating away and back
 * recovers a stuck error automatically).
 *
 * `label` customises the headline (e.g. "Canvas crashed", "Comparison failed
 * to render"); defaults to a generic message.
 */
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode; label?: string },
  { error: Error | null }
> {
  constructor(props: { children: React.ReactNode; label?: string }) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Surface in the console so a render crash is debuggable even when the
    // inline fallback only shows the message.
    console.error(`[ErrorBoundary${this.props.label ? ` ${this.props.label}` : ''}] caught`, error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-3 text-sm p-6">
          <p className="text-danger font-semibold">{this.props.label ?? 'Something went wrong'}</p>
          <p className="text-muted max-w-sm text-center font-mono text-xs">
            {this.state.error.message}
          </p>
          <button
            onClick={() => this.setState({ error: null })}
            className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-accent hover:border-accent transition-colors"
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
