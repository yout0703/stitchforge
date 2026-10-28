import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeftIcon, EyeIcon, EyeSlashIcon, FileCodeIcon, ImageIcon, XIcon } from '@phosphor-icons/react'
import type { StartIntent } from '../lib/startIntent'

export function AuthDialog({ intent, onSubmit, onClose, onBack }: {
  intent: StartIntent | null
  onSubmit: (register: boolean, credentials: { email: string; password: string }) => Promise<void>
  onClose: () => void
  onBack: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  useEffect(() => { dialog.current?.showModal() }, [])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setBusy(true); setError('')
    try { await onSubmit(register, { email: String(data.get('email')), password: String(data.get('password')) }) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '登录失败，请重试') }
    finally { setBusy(false) }
  }
  const ContextIcon = intent?.kind === 'image' ? ImageIcon : FileCodeIcon
  return <dialog ref={dialog} className="auth-dialog" aria-labelledby="account-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="auth-dialog-top"><button type="button" className="auth-back" disabled={busy} onClick={onBack}><ArrowLeftIcon size={16} aria-hidden="true" />{intent?.file ? '返回文件选择' : '返回首页'}</button><button className="auth-close" type="button" aria-label="关闭登录" disabled={busy} onClick={onClose}><XIcon size={20} aria-hidden="true" /></button></div>
    <section className="account-card auth-card" aria-labelledby="account-title">
      <h2 id="account-title">{register ? '创建你的账号' : '登录，继续你的创作'}</h2>
      <p>{intent ? '登录后将回到刚才的操作，文件不需要重新选择。' : '打开图纸库，继续上次的作品。'}</p>
      {intent?.file && <div className="auth-file-context"><ContextIcon size={24} aria-hidden="true" /><div><strong title={intent.file.name}>{intent.file.name}</strong><small>已选文件 · 登录后继续{intent.kind === 'oxs' ? '导入' : '生成确认'}</small></div></div>}
      <form onSubmit={submit}>
        <label>邮箱<input autoFocus name="email" autoComplete="username" type="email" maxLength={254} placeholder="you@example.com" required disabled={busy} /></label>
        <label>密码<span className="password-field"><input name="password" aria-label="密码" autoComplete={register ? 'new-password' : 'current-password'} type={showPassword ? 'text' : 'password'} minLength={10} maxLength={128} placeholder="至少 10 个字符" required disabled={busy} /><button type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} disabled={busy} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeSlashIcon size={20} aria-hidden="true" /> : <EyeIcon size={20} aria-hidden="true" />}</button></span></label>
        {register && <label className="consent-field"><input name="consent" type="checkbox" required disabled={busy} /><span>我已阅读并同意 <a href="/terms.html" target="_blank" rel="noopener">服务条款</a> 与 <a href="/privacy.html" target="_blank" rel="noopener">隐私政策</a></span></label>}
        {error && <p className="api-error" role="alert">{error}</p>}
        <button className="primary-button" type="submit" disabled={busy}>{busy ? '请稍候…' : register ? '注册并继续' : intent ? '登录并继续' : '登录'}</button>
      </form>
      <button className="account-switch" type="button" disabled={busy} onClick={() => { setRegister(!register); setError(''); setShowPassword(false) }}>{register ? '已有账号？去登录' : '还没有账号？免费注册'}</button>
      <small>OXS / XSD 导入、查看与编辑免费；图片生成成功消耗 1 次。</small>
    </section>
  </dialog>
}
