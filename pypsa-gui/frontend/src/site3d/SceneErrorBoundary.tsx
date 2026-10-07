// An error boundary whose fallback is any React node, so it works inside
// the r3f scene graph (the app's components/ErrorBoundary renders DOM, which
// r3f cannot). Used to keep a hero model's parametric stand-in when the
// model fails to load (Phase 2 plan Task 3.5). No three.
import { Component, type ReactNode } from 'react'

interface Props { fallback: ReactNode; children: ReactNode; /** A change clears a caught error (e.g. the site reopens). */ resetKey?: unknown }
interface State { failed: boolean; key: unknown }

export default class SceneErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, key: this.props.resetKey }
  static getDerivedStateFromError(): Partial<State> { return { failed: true } }
  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null
  }
  componentDidCatch(error: unknown): void {
    console.warn('[site3d] a hero model failed; keeping the parametric form:', error instanceof Error ? error.message : error)
  }
  render(): ReactNode { return this.state.failed ? this.props.fallback : this.props.children }
}
