import { useEffect, useRef } from 'react'
import { ArrowRightIcon, CheckCircleIcon, FileCodeIcon, ImageIcon, PlusIcon, XIcon } from '@phosphor-icons/react'
import { BrandMark } from './BrandMark'
import { FilePicker } from './FilePicker'
import type { ImportKind, StartIntent } from '../lib/startIntent'
import stitchStudioHero from '../assets/stitch-studio-hero.jpg'
import './PublicHome.css'

export function PublicHome({ onStart, onSignIn, error, onRetry }: {
  onStart: (kind: StartIntent['kind']) => void
  onSignIn: () => void
  error: string
  onRetry: () => void
}) {
  return <><a className="skip-link" href="#main-content">跳到开始制作</a><main className="public-home" id="main-content">
    <header className="home-header">
      <a className="home-brand" href="/" aria-label="StitchForge 首页"><BrandMark className="home-brand-mark" /><span>StitchForge<small>十字绣工作台</small></span></a>
      <button className="quiet-button" type="button" onClick={onSignIn}>登录 / 注册<ArrowRightIcon size={16} aria-hidden="true" /></button>
    </header>
    <section className="home-hero" aria-labelledby="home-title">
      <div className="home-hero-copy">
        <p className="home-eyebrow">让喜欢的画面，成为手中的作品</p>
        <h1 id="home-title">从一张图开始，<br />一针一线，绣出喜欢。</h1>
        <p className="home-description">打开已有的十字绣图纸，或把照片与插画转成针格。查线色、在线编辑，再下载属于你的图纸。</p>
        <div className="home-actions" aria-label="开始制作">
          <button className="home-start primary" type="button" onClick={() => onStart('oxs')}><FileCodeIcon size={24} aria-hidden="true" /><span><strong>导入已有图纸</strong><small>OXS / XSD · 免费查看与编辑</small></span><ArrowRightIcon size={20} aria-hidden="true" /></button>
          <button className="home-start" type="button" onClick={() => onStart('image')}><ImageIcon size={24} aria-hidden="true" /><span><strong>图片转图纸</strong><small>JPG / PNG · 生成成功消耗 1 次</small></span><ArrowRightIcon size={20} aria-hidden="true" /></button>
        </div>
        <p className="home-start-note"><CheckCircleIcon size={16} aria-hidden="true" />先选文件，登录后继续。确认前不会上传。</p>
        <button className="home-blank-link" type="button" onClick={() => onStart('blank')}><PlusIcon size={16} aria-hidden="true" />也可以从空白图纸开始</button>
      </div>
      <figure className="home-visual"><img src={stitchStudioHero} alt="蓝色小鸟与花朵十字绣作品、绣线和剪刀" fetchPriority="high" /><figcaption><strong>把喜欢留在针脚里</strong><span>针格、线色与成品尺寸，清楚可查。</span></figcaption></figure>
    </section>
    {error && <div className="home-service-error" role="alert"><span>{error}</span><button className="quiet-button" type="button" onClick={onRetry}>重新连接</button></div>}
    <section className="home-workflow" aria-label="制作流程">
      <div><span>01</span><p><strong>选好你的素材</strong><small>已有图纸或喜欢的图片，都能开始。</small></p></div>
      <div><span>02</span><p><strong>登录，接着制作</strong><small>已选文件保留在页面，无需再选一遍。</small></p></div>
      <div><span>03</span><p><strong>查看、编辑与下载</strong><small>查找线色，导出 PNG、OXS 或 XSD。</small></p></div>
    </section>
    <footer className="home-footer"><span>© 2026 StitchForge</span><div><a href="/privacy.html">隐私政策</a><a href="/terms.html">服务条款</a></div></footer>
  </main></>
}

export function StartFileDialog({ intent, onChange, onContinue, onClose }: {
  intent: StartIntent
  onChange: (file: File | null) => void
  onContinue: () => void
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const chart = intent.kind === 'oxs'
  return <dialog ref={dialog} className="import-dialog start-file-dialog" aria-labelledby="start-file-title"
    onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="import-heading"><div><h2 id="start-file-title">{chart ? '导入已有图纸' : '图片转图纸'}</h2><p>先选好文件，登录后继续；不需要重新选择。</p></div><button type="button" aria-label="关闭文件选择" onClick={onClose}><XIcon size={20} aria-hidden="true" /></button></div>
    <div className="start-file-content">
      <FilePicker kind={intent.kind as ImportKind} file={intent.file} onChange={onChange} />
      <p className="start-file-privacy">{chart ? '免费导入，支持 OXS 和 Pattern Maker v4 XSD 全针图纸。' : '生成成功消耗 1 次，失败不扣次数。新账号需先获取生成次数。'} 文件仅保留在当前页面，刷新或关闭页面后需重新选择。</p>
      <div className="import-actions"><button type="button" onClick={onClose}>返回首页</button><button className="primary-button" type="button" disabled={!intent.file} onClick={onContinue}>登录后继续<ArrowRightIcon size={16} aria-hidden="true" /></button></div>
    </div>
  </dialog>
}
