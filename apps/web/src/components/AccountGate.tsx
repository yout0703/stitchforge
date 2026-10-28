import { useCallback, useEffect, useRef, useState } from 'react'
import App from '../App'
import { accountRequest, apiFetch, type User } from '../lib/account'
import type { StartIntent } from '../lib/startIntent'
import { AuthDialog } from './AuthDialog'
import { PublicHome, StartFileDialog } from './PublicHome'

export function AccountGate() {
  const authEpoch = useRef(0)
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [step, setStep] = useState<'home' | 'file' | 'auth'>('home')
  const [intent, setIntent] = useState<StartIntent | null>(null)
  const consumeIntent = useCallback(() => setIntent(null), [])
  const refresh = useCallback(async () => {
    const epoch = authEpoch.current
    try {
      const response = await apiFetch('/api/v1/auth/me')
      if (response.status === 401) { if (epoch === authEpoch.current) { setUser(null); setError('') }; return }
      if (!response.ok) throw new Error('服务暂时不可用，请稍后重试')
      const nextUser = await response.json() as User
      if (epoch === authEpoch.current) { setUser(nextUser); setError('') }
    } catch (reason) { if (epoch === authEpoch.current) setError(reason instanceof Error ? reason.message : '无法连接服务') }
    finally { if (epoch === authEpoch.current) setLoading(false) }
  }, [])
  useEffect(() => {
    let active = true
    void apiFetch('/api/v1/auth/me').then(async response => {
      if (response.status === 401) return null
      if (!response.ok) throw new Error('服务暂时不可用，请稍后重试')
      return await response.json() as User
    }).then(value => { if (active) setUser(value) }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : '无法连接服务')
    }).finally(() => { if (active) setLoading(false) })
    const expired = () => { authEpoch.current += 1; setUser(null); setIntent(null); setStep('auth') }
    window.addEventListener('stitchforge:session-expired', expired)
    return () => { active = false; window.removeEventListener('stitchforge:session-expired', expired) }
  }, [])

  function start(kind: StartIntent['kind']) {
    setIntent(current => current?.kind === kind ? current : { kind, file: null })
    setStep(kind === 'blank' ? 'auth' : 'file')
  }

  if (loading) return <main id="main-content" className="account-page account-loading"><div className="brand-loader" aria-hidden="true" /><p role="status">正在打开 StitchForge…</p></main>
  if (user) return <App key={user.user_id} user={user} initialEntry={intent} onEntryConsumed={consumeIntent} onAccountRefresh={refresh} onLogout={async () => {
    authEpoch.current += 1
    await accountRequest('/auth/logout', {}); setUser(null); setIntent(null); setStep('home'); setError('')
  }} />
  return <>
    <PublicHome onStart={start} onSignIn={() => { setIntent(null); setStep('auth') }} error={error} onRetry={() => { void refresh() }} />
    {step === 'file' && intent && intent.kind !== 'blank' && <StartFileDialog intent={intent}
      onChange={file => setIntent({ ...intent, file })} onContinue={() => setStep('auth')} onClose={() => setStep('home')} />}
    {step === 'auth' && <AuthDialog intent={intent} onClose={() => setStep('home')}
      onBack={() => setStep(intent?.file ? 'file' : 'home')}
      onSubmit={async (register, credentials) => {
        authEpoch.current += 1
        setUser(await accountRequest<User>(register ? '/auth/register' : '/auth/login', credentials))
        setStep('home'); setError('')
      }} />}
  </>
}
