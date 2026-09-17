import { useEffect, useRef, useState } from 'react'

export function BlankProjectDialog({ onCreate, onClose }: {
  onCreate: (name: string, width: number, height: number) => Promise<void>
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal() }, [])
  return <dialog ref={dialog} className="import-dialog" aria-labelledby="blank-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="import-heading">
      <div><h2 id="blank-title">新建空白图纸</h2><p>设置针格尺寸，使用基础配色开始绘制。</p></div>
      <button type="button" aria-label="关闭新建图纸" disabled={busy} onClick={onClose}>×</button>
    </div>
    <form onSubmit={async event => {
      event.preventDefault()
      const values = new FormData(event.currentTarget)
      setBusy(true); setError('')
      try {
        await onCreate(String(values.get('name')).trim(), Number(values.get('width')), Number(values.get('height')))
      } catch (reason) { setError(reason instanceof Error ? reason.message : '创建失败，请重试') }
      finally { setBusy(false) }
    }}>
      <label className="file-field"><span>图纸名称</span><input autoFocus name="name" defaultValue="未命名图纸" maxLength={120} required disabled={busy} /></label>
      <div className="blank-dimensions">
        <label className="file-field"><span>宽度（格）</span><input name="width" type="number" min={1} max={600} step={1} defaultValue={64} required disabled={busy} /></label>
        <label className="file-field"><span>高度（格）</span><input name="height" type="number" min={1} max={600} step={1} defaultValue={64} required disabled={busy} /></label>
      </div>
      <p className="import-explanation">免费创建 · 含 8 种基础自定义颜色（非品牌绣线色卡）。创建后可在线色面板切换颜色。</p>
      {error && <p className="import-error" role="alert">{error}</p>}
      <div className="import-actions"><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary-button" disabled={busy} type="submit">{busy ? '创建中…' : '创建并开始绘制'}</button></div>
    </form>
  </dialog>
}
