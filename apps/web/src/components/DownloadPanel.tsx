import { useEffect, useRef, useState } from 'react'
import { XIcon } from '@phosphor-icons/react'
import { downloadChart } from '../lib/api'

export function DownloadPanel({ title, dirty, onPrepare, onClose }: {
  title: string
  dirty: boolean
  onPrepare: () => Promise<{ projectId: string; headRevisionId: string }>
  onClose: () => void
}) {
  const [busy, setBusy] = useState<'oxs' | 'xsd' | 'png' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [file, setFile] = useState<{ url: string; name: string; format: 'oxs' | 'xsd' | 'png' } | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const objectUrl = useRef<string | null>(null)
  const mounted = useRef(false)
  const preparing = useRef(false)
  useEffect(() => {
    mounted.current = true
    dialog.current?.showModal()
    return () => {
      mounted.current = false
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    }
  }, [])

  async function prepare(format: 'oxs' | 'xsd' | 'png') {
    if (preparing.current) return
    preparing.current = true
    setBusy(format)
    setError(null)
    setFile(null)
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    try {
      const session = await onPrepare()
      const blob = await downloadChart(session.projectId, session.headRevisionId, format)
      if (!mounted.current) return
      const url = URL.createObjectURL(blob)
      objectUrl.current = url
      // Control characters are intentionally removed from download filenames.
      // eslint-disable-next-line no-control-regex
      const name = `${title.replace(/[\u0000-\u001f\u007f/\\:*?"<>|]/g, '_').slice(0, 120).trim() || 'chart'}.${format}`
      setFile({ url, name, format })
      // Retain a real download link for browsers requiring a fresh user gesture.
      const link = window.document.createElement('a')
      link.href = url
      link.download = name
      window.document.body.append(link)
      link.click()
      link.remove()
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : '下载失败，请重试')
    } finally {
      preparing.current = false
      if (mounted.current) setBusy(null)
    }
  }

  return <dialog ref={dialog} className="import-dialog download-dialog" aria-labelledby="download-title"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="import-heading">
      <div><h2 id="download-title">下载图纸</h2><p>免费下载，不消耗生成次数。</p></div>
      <button type="button" aria-label="关闭下载" disabled={!!busy} onClick={onClose}><XIcon size={20} aria-hidden="true" /></button>
    </div>
    <div className="download-content">
      {dirty && <p className="billing-notice">下载前会先保存当前修改。</p>}
      <div className="download-option">
        <h3>完整图纸图片（PNG）</h3>
        <p>包含全图、彩色针格、字母符号、坐标刻度，以及符号与绣线色号对照表。保留全部颜色，不受当前缩放或高亮影响。</p>
        <button className="primary-button" type="button" disabled={!!busy} onClick={() => void prepare('png')}>下载完整图片</button>
      </div>
      <div className="download-option">
        <h3>OXS 图纸文件</h3>
        <p>包含针格、线色、股数和绣布 CT，可重新导入或在兼容软件中打开。</p>
        <p className="download-note">OXS 不包含编辑历史、区域锁定或来源记录；其他软件可能使用自己的符号样式。</p>
        <button className="quiet-button" type="button" disabled={!!busy} onClick={() => void prepare('oxs')}>下载 OXS</button>
      </div>
      <div className="download-option">
        <h3>XSD 图纸文件</h3>
        <p>Pattern Maker v4 全针格式，包含针格、线色、股数和绣布 CT。</p>
        <p className="download-note">不包含编辑历史、区域锁定、来源记录、裁布边距和针方向。标题和作者最多 40 字节；自定义线色的完整名称保存在备注中。符号和中文显示取决于目标软件，尚未实测 Pattern Maker / Saga 导入。</p>
        <button className="quiet-button" type="button" disabled={!!busy} onClick={() => void prepare('xsd')}>下载 XSD</button>
      </div>
      {busy && <p role="status">正在准备{busy === 'png' ? '完整图片，大图可能需要一些时间' : `${busy.toUpperCase()} 文件`}…</p>}
      {error && <p className="import-error" role="alert">{error}</p>}
      {file && <div className="download-ready" role="status">
        <p>文件已准备好。如果没有自动下载，请点击下方保存。</p>
        <a className="primary-button" href={file.url} download={file.name}>保存{file.format === 'png' ? ' PNG 图片' : ` ${file.format.toUpperCase()} 文件`}</a>
        {file.format === 'png' && <a href={file.url} target="_blank" rel="noopener">打开图片预览</a>}
        <small>手机上可使用浏览器的下载、保存到文件或图片预览中的保存功能。</small>
      </div>}
    </div>
  </dialog>
}
