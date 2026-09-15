import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import App from '../App'
import { accountRequest, apiFetch, type User } from '../lib/account'

export function AccountGate() {
  const authEpoch = useRef(0)
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    const epoch = authEpoch.current
    try {
      const response = await apiFetch('/api/v1/auth/me')
      if (response.status === 401) { if (epoch === authEpoch.current) setUser(null); return }
      if (!response.ok) throw new Error('服务暂时不可用，请稍后重试')
      const nextUser = await response.json() as User
      if (epoch === authEpoch.current) setUser(nextUser)
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
    const expired = () => { authEpoch.current += 1; setUser(null); setError('登录已过期，请重新登录。') }
    window.addEventListener('stitchforge:session-expired', expired)
    return () => { active = false; window.removeEventListener('stitchforge:session-expired', expired) }
  }, [])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); authEpoch.current += 1
    const data = new FormData(event.currentTarget)
    try {
      setUser(await accountRequest<User>(register ? '/auth/register' : '/auth/login', {
        email: data.get('email'), password: data.get('password'),
      }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '登录失败') }
    finally { setBusy(false) }
  }
  if (loading) return <main className="account-page"><p role="status">正在打开你的工作台…</p></main>
  if (user) return <App key={user.user_id} user={user} onAccountRefresh={refresh} onLogout={async () => {
    authEpoch.current += 1
    await accountRequest('/auth/logout', {}); setUser(null); setError('')
  }} />
  return <main className="account-page">
    <section className="account-intro"><span className="brand-mark">SF</span><p>StitchForge · 十字绣工作台</p>
      <h1>把喜欢的画面，<br />一针一线绣出来。</h1>
      <p>上传图片生成图纸，或免费导入 OXS。<br />在手机、平板和电脑上，打开自己的图纸接着绣。</p>
      <div className="account-benefits"><span>OXS 导入免费</span><span>看图不扣次数</span><span>图片生成按次使用</span></div>
    </section>
    <section className="account-card" aria-labelledby="account-title">
      <h2 id="account-title">{register ? '创建你的账号' : '登录你的工作台'}</h2>
      <p>图纸与原图保存在你的账号下。</p>
      <form onSubmit={submit}>
        <label>邮箱<input autoComplete="username" name="email" type="email" maxLength={254} required disabled={busy} placeholder="you@example.com" /></label>
        <label>密码<input autoComplete={register ? 'new-password' : 'current-password'} name="password" type="password" minLength={10} maxLength={128} required disabled={busy} placeholder="至少 10 个字符" /></label>
        {error && <p role="alert" className="api-error">{error}</p>}
        <button className="primary-button" disabled={busy} type="submit">{busy ? '请稍候…' : register ? '注册并开始' : '登录'}</button>
      </form>
      <button className="account-switch" disabled={busy} onClick={() => { setRegister(!register); setError('') }}>{register ? '已有账号？去登录' : '还没有账号？免费注册'}</button>
      <small>注册后即可免费导入与查看 OXS；生成图片图纸需先获取次数。</small>
    </section>
  </main>
}
