import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AccountGate } from './components/AccountGate'
import { AppErrorBoundary } from './components/AppErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary><AccountGate /></AppErrorBoundary>
  </StrictMode>,
)
