import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import App from '../App'
import { accountRequest, apiFetch, type User } from '../lib/account'
import stitchStudioHero from '../assets/stitch-studio-hero.jpg'
import { BrandMark } from './BrandMark'
import { EyeIcon, EyeSlashIcon } from '@phosphor-icons/react'

export function AccountGate() {
  const authEpoch = useRef(0)
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showPassword, setShowPassword] = useState(false)
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
  if (loading) return <main id="main-content" className="account-page account-loading"><div className="brand-loader" aria-hidden="true" /><p role="status">正在打开你的工作台…</p></main>
  if (user) return <App key={user.user_id} user={user} onAccountRefresh={refresh} onLogout={async () => {
    authEpoch.current += 1
    await accountRequest('/auth/logout', {}); setUser(null); setError('')
  }} />
  return <><a className="skip-link" href="#main-content">跳到登录表单</a><main id="main-content" className="account-page">
    <section className="account-intro"><div className="account-brand"><BrandMark className="brand-mark" /><p>StitchForge <span>十字绣工作台</span></p></div>
      <p className="account-eyebrow">从像素到针脚，尺寸与线色都有据可查</p>
      <h1>把喜欢的画面，<br />变成能绣的图纸。</h1>
      <p className="account-copy">上传图片自动打版，或免费导入 OXS / XSD。图纸、原图与版本记录跟随账号保存，换一台设备也能继续。</p>
      <figure className="account-visual">
        <img src={stitchStudioHero} alt="蓝色小鸟与花朵十字绣作品、绣线和剪刀" fetchPriority="high" />
        <figcaption>
          <strong>从原图到可编辑针格</strong>
          <span>规划尺寸、匹配线色，再导出清晰图纸。</span>
        </figcaption>
      </figure>
      <div className="account-benefits"><span>自动打版</span><span>OXS / XSD 导入</span><span>在线编辑与导出</span></div>
    </section>
    <section className="account-card" aria-labelledby="account-title">
      <h2 id="account-title">{register ? '创建你的账号' : '登录你的工作台'}</h2>
      <p>图纸与原图保存在你的账号下。</p>
      <form onSubmit={submit}>
        <label>邮箱<input autoComplete="username" name="email" type="email" maxLength={254} required disabled={busy} placeholder="you@example.com" /></label>
        <label>密码<span className="password-field"><input autoComplete={register ? 'new-password' : 'current-password'} name="password" aria-label="密码" type={showPassword ? 'text' : 'password'} minLength={10} maxLength={128} required disabled={busy} placeholder="至少 10 个字符" />
          <button type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} disabled={busy} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeSlashIcon size={20} aria-hidden="true" /> : <EyeIcon size={20} aria-hidden="true" />}</button>
        </span></label>
        {register && <label className="consent-field"><input name="consent" type="checkbox" required disabled={busy} /><span>我已阅读并同意 <a href="/terms.html" target="_blank">服务条款</a> 与 <a href="/privacy.html" target="_blank">隐私政策</a></span></label>}
        {error && <p role="alert" className="api-error">{error}</p>}
        <button className="primary-button" disabled={busy} type="submit">{busy ? '请稍候…' : register ? '注册并开始' : '登录'}</button>
      </form>
      <button className="account-switch" disabled={busy} onClick={() => { setRegister(!register); setError(''); setShowPassword(false) }}>{register ? '已有账号？去登录' : '还没有账号？免费注册'}</button>
      <small>注册后即可免费导入与查看 OXS / XSD；生成图片图纸需先获取次数。</small>
      <footer className="account-legal"><span>© 2026 StitchForge</span><a href="/privacy.html">隐私政策</a><a href="/terms.html">服务条款</a></footer>
    </section>
  </main></>
}
