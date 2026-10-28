import { useEffect, useId, useRef, useState } from 'react'
import { UploadSimpleIcon, ArrowsClockwiseIcon, CheckCircleIcon, FileCodeIcon, ImageIcon, XIcon } from '@phosphor-icons/react'
import { formatFileSize, sourceFileError, type ImportKind } from '../lib/startIntent'
import './FilePicker.css'

export function FilePicker({ kind, file, onChange, disabled = false }: {
  kind: ImportKind
  file: File | null
  onChange: (file: File | null) => void
  disabled?: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const id = useId()
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const preview = useRef<HTMLImageElement>(null)
  useEffect(() => {
    if (kind !== 'image' || !file || !preview.current) return
    const url = URL.createObjectURL(file)
    preview.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [file, kind])
  const chart = kind === 'oxs'

  function choose(files: FileList | null) {
    if (disabled || !files?.length) return
    if (files.length !== 1) { setError('每次请选择一个文件。'); onChange(null); return }
    const selected = files[0]
    const problem = sourceFileError(selected, kind)
    setError(problem ?? '')
    onChange(problem ? null : selected)
  }

  return <div className="source-picker">
    <span className="source-picker-label" id={`${id}-label`}>{chart ? 'OXS / XSD 文件' : '效果原图'}</span>
    <div className={`source-dropzone${dragging ? ' is-dragging' : ''}${file ? ' has-file' : ''}${disabled ? ' is-disabled' : ''}`}
      onDragOver={event => {
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault(); if (!disabled) setDragging(true)
      }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }}
      onDrop={event => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files) }}>
      {file ? <>
        <div className="source-file-preview" aria-hidden="true">
          {kind === 'image' ? <img ref={preview} alt="" /> : chart ? <FileCodeIcon size={30} /> : <ImageIcon size={30} />}
        </div>
        <div className="source-file-copy"><strong title={file.name}>{file.name}</strong><span>{file.name.split('.').pop()?.toUpperCase()} · {formatFileSize(file.size)}</span><small><CheckCircleIcon size={14} aria-hidden="true" />已选择，确认后上传</small></div>
        <div className="source-file-actions">
          <button type="button" disabled={disabled} onClick={() => input.current?.click()}><ArrowsClockwiseIcon size={16} aria-hidden="true" />更换文件</button>
          <button type="button" aria-label="移除已选文件" disabled={disabled} onClick={() => { onChange(null); setError('') }}><XIcon size={17} aria-hidden="true" /></button>
        </div>
      </> : <button className="source-choose-button" type="button" disabled={disabled} onClick={() => input.current?.click()} aria-describedby={`${id}-hint`}>
        <span className="source-upload-icon"><UploadSimpleIcon size={26} aria-hidden="true" /></span>
        <strong>{chart ? '选择图纸文件' : '选择图片'}</strong>
        <span className="source-drop-hint">也可以将文件拖到这里</span>
        <span className="source-choose-label">浏览文件</span>
      </button>}
      <input ref={input} className="source-native-input" tabIndex={-1} aria-hidden="true" name="image" type="file"
        accept={chart ? '.oxs,.xsd' : 'image/png,image/jpeg'} disabled={disabled}
        onChange={event => { choose(event.target.files); event.target.value = '' }} />
    </div>
    <small id={`${id}-hint`} className="source-picker-hint">{chart ? 'OXS / XSD · 最大 16 MiB' : 'JPG / PNG · 最大 20 MiB'}<span>确认前仅保留在当前页面</span></small>
    {error && <p role="alert" className="import-error">{error}</p>}
  </div>
}
