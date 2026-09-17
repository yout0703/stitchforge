import { useEffect, useRef, useState } from 'react'
import { ApiError, cancelGeneration, createProject, deleteProject, enqueueProjectImage, recoverGeneration, type GenerationJob } from '../lib/api'

interface PendingGeneration { requestKey: string; projectId: string; headRevisionId: string; sourceSha256: string }
const STAGES: Record<string, string> = {
  queued: '正在排队…', starting: '正在准备图片…', resuming: '正在恢复生成任务…',
  normalization: '正在检查图片…', planning: '正在分析针格与结构…', sampling: '正在处理针格与底布…',
  palette: '正在匹配线色…', quality: '正在检查图纸质量…', persisting: '正在保存图纸…',
  completed: '图纸已生成', cancelled: '生成已取消，本次不扣次数。',
}

export function useGeneration(userId: string) {
  const key = `stitchforge.pendingGeneration.${userId}`
  const [pending, setPending] = useState<PendingGeneration | null>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? 'null')
      return value && ['requestKey', 'projectId', 'headRevisionId', 'sourceSha256'].every(field => typeof value[field] === 'string') ? value : null
    } catch { return null }
  })
  const [job, setJob] = useState<GenerationJob | null>(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [abandoning, setAbandoning] = useState(false)
  const [canAbandon, setCanAbandon] = useState(false)
  const busy = useRef(false)
  const [retry, setRetry] = useState(0)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  function remember(value: PendingGeneration | null) {
    if (value) localStorage.setItem(key, JSON.stringify(value))
    else localStorage.removeItem(key)
    if (mounted.current) setPending(value)
  }

  useEffect(() => {
    if (!pending || uploading || abandoning) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const current = await recoverGeneration(pending!.requestKey)
        if (cancelled) return
        setJob(current)
        setCanAbandon(false)
        setError('')
        setStatus(current.state === 'CANCELLED' ? '生成已取消，本次不扣次数。' : current.state === 'FAILED' ? '生成未完成' : current.state === 'SUCCEEDED' ? '图纸已生成' : current.state === 'CANCEL_REQUESTED' ? '正在取消生成…' : STAGES[current.stage] ?? '正在生成图纸…')
        if (current.state === 'SUCCEEDED') return
        if (current.state === 'FAILED' || current.state === 'CANCELLED') {
          if (current.state === 'FAILED') setError(current.error?.message ?? '生成失败，本次不扣次数。')
          localStorage.removeItem(key)
          setPending(null)
          return
        }
      } catch (reason) {
        if (cancelled) return
        if (reason instanceof ApiError && reason.code === 'NOT_FOUND') {
          setCanAbandon(true)
          setStatus('图片尚未提交成功')
          setError('尚未找到生成任务。可重新提交同一张图片，或放弃本次上传后选择其他图片。')
          return
        }
        setError('连接暂时中断，正在查询上次生成结果；请勿重复创建任务。')
      }
      if (!cancelled) timer = setTimeout(poll, 1500)
    }
    void poll()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [pending, uploading, abandoning, retry, key])

  async function start(file: File) {
    if (busy.current) throw new Error('正在处理上次操作，请稍候')
    busy.current = true
    setUploading(true)
    setCanAbandon(false)
    setJob(null)
    setError('')
    setStatus('正在上传图片…')
    try {
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
      const sourceSha256 = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
      let record = pending
      if (record && job?.state === 'SUCCEEDED') record = null
      if (record && record.sourceSha256 !== sourceSha256) throw new Error('请先确认上次生成的结果，再上传另一张图片。')
      if (!record) {
        const project = await createProject(file.name.replace(/\.[^.]+$/, '') || '图片图纸')
        record = { requestKey: crypto.randomUUID(), sourceSha256,
          projectId: project.project.project_id, headRevisionId: project.initial_revision.revision_id }
      }
      // Persist before submission, so a lost response can be recovered without another charge.
      remember(record)
      const queued = await enqueueProjectImage(record.projectId, record.headRevisionId, record.requestKey, file)
      if (mounted.current) { setJob(queued); setRetry(value => value + 1) }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '上传失败')
      throw reason
    } finally { busy.current = false; if (mounted.current) setUploading(false) }
  }

  async function abandonUpload() {
    if (!pending || busy.current) return
    busy.current = true
    setAbandoning(true)
    setError('')
    try {
      // A lost upload response may hide a real task: query again before discarding.
      try {
        const current = await recoverGeneration(pending.requestKey)
        if (mounted.current) {
          setJob(current); setCanAbandon(false); setRetry(value => value + 1)
          setStatus('已找到生成任务，请查看结果或取消生成。')
        }
        return
      } catch (reason) {
        if (!(reason instanceof ApiError) || reason.code !== 'NOT_FOUND') throw reason
      }
      // The dedicated empty project fences any delayed enqueue. A head check keeps
      // a result published during this race safe; deletion is recoverable in trash.
      await deleteProject(pending.projectId, pending.headRevisionId)
      remember(null)
      if (mounted.current) {
        setJob(null); setCanAbandon(false); setStatus('本次上传已放弃，可以重新选择图片。')
      }
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof ApiError && reason.code === 'REVISION_CONFLICT'
          ? '图纸已有新版本，已保留。正在重新查询生成结果。'
          : '暂时无法确认上传状态，记录已保留，请恢复连接后重试。')
        setRetry(value => value + 1)
      }
    } finally {
      busy.current = false
      if (mounted.current) setAbandoning(false)
    }
  }

  async function cancel() {
    if (!pending || busy.current) return
    try {
      const current = job ?? await recoverGeneration(pending.requestKey)
      const cancelled = await cancelGeneration(current.job_id)
      if (mounted.current) { setJob(cancelled); setRetry(value => value + 1) }
    } catch (reason) {
      if (mounted.current) {
        if (reason instanceof ApiError && reason.code === 'NOT_FOUND') setCanAbandon(true)
        setError(reason instanceof Error ? reason.message : '取消失败，请继续查询')
      }
    }
  }

  function acknowledge() { remember(null); setJob(null); setStatus(''); setError(''); setCanAbandon(false) }
  return { start, cancel, abandonUpload, canAbandon, abandoning, acknowledge, job, status, error, pending: !!pending, uploading,
    retry: () => setRetry(value => value + 1) }
}
