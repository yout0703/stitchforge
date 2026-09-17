import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { failed: boolean }

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('StitchForge render failed', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <main className="fatal-error" id="main-content">
      <span className="brand-mark" aria-hidden="true">SF</span>
      <p>工作台遇到问题</p>
      <h1>当前页面没有正确加载</h1>
      <p>你的本地草稿仍保存在这台设备上。刷新页面后可以继续尝试。</p>
      <button className="primary-button" type="button" onClick={() => window.location.reload()}>重新加载</button>
    </main>
  }
}
