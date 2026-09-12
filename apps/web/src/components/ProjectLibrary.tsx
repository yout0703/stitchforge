import { useEffect, useState } from 'react'
import { deleteProject, listProjects, renameProject, restoreProject } from '../lib/api'
import type { LibraryProject } from '../lib/types'

interface Props {
  onOpen: (id: string) => Promise<void>
  onRemoved: (id: string) => void
  onRenamed: (id: string, name: string, head: string) => void
  onBusy: (busy: boolean) => void
}

export function ProjectLibrary({ onOpen, onRemoved, onRenamed, onBusy }: Props) {
  const [search, setSearch] = useState('')
  const [deleted, setDeleted] = useState(false)
  const [sort, setSort] = useState('updated')
  const [offset, setOffset] = useState(0)
  const [reload, setReload] = useState(0)
  const [result, setResult] = useState<{ items: LibraryProject[]; total: number }>({ items: [], total: 0 })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<LibraryProject | null>(null)
  const [name, setName] = useState('')

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(() => {
      listProjects({ search, deleted, sort, offset }).then((data) => {
        if (cancelled) return
        if (offset > 0 && data.items.length === 0) {
          setOffset(Math.max(0, Math.ceil(data.total / 24) - 1) * 24)
          return
        }
        setResult(data)
        setLoading(false)
      }).catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '图纸库读取失败')
          setLoading(false)
        }
      })
    }, 150)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [search, deleted, sort, offset, reload])

  function refresh() { setLoading(true); setError(null); setReload((value) => value + 1) }
  async function action(callback: () => Promise<void>) {
    setBusy(true); onBusy(true); setError(null)
    try { await callback() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败，请重试') }
    finally { setBusy(false); onBusy(false) }
  }

  return <section className="project-library" aria-label="图纸库">
    <div className="library-heading">
      <div><h1>我的图纸</h1><p>新建、图片生成和 OXS 导入的作品，都保存在这里。</p></div>
      <span className="storage-badge">本机保存 · SQLite</span>
    </div>
    <div className="library-controls">
      <div className="view-tabs">
        <button type="button" className={!deleted ? 'active' : ''} aria-pressed={!deleted} disabled={busy}
          onClick={() => { if (!deleted) return; setDeleted(false); setOffset(0); setLoading(true); setError(null) }}>全部图纸</button>
        <button type="button" className={deleted ? 'active' : ''} aria-pressed={deleted} disabled={busy}
          onClick={() => { if (deleted) return; setDeleted(true); setOffset(0); setLoading(true); setError(null) }}>回收站</button>
      </div>
      <input type="search" aria-label="搜索图纸" placeholder="搜索图纸名称" maxLength={256} value={search} disabled={busy}
        onChange={(event) => { setSearch(event.target.value); setOffset(0); setLoading(true); setError(null) }} />
      <select aria-label="图纸排序" value={sort} disabled={busy} onChange={(event) => {
        setSort(event.target.value); setOffset(0); setLoading(true); setError(null)
      }}>
        <option value="updated">最近更新</option><option value="created">最近创建</option><option value="name">名称排序</option>
      </select>
      <button type="button" className="quiet-button" disabled={busy} onClick={refresh}>刷新列表</button>
    </div>
    {error && <p className="library-error" role="alert">{error}</p>}
    {loading ? <p role="status" className="library-empty">正在读取图纸…</p> : <>
      <div className="library-summary">{deleted ? '回收站' : '图纸'} · {result.total} 份{deleted && ' · 可随时恢复，原图和历史版本仍保留'}</div>
      {result.items.length === 0 ? <div className="library-empty">
        <h2>{search ? '没有找到匹配的图纸' : deleted ? '回收站是空的' : '还没有图纸'}</h2>
        <p>{search ? '换个名称试试，或清空搜索。' : deleted ? '移除的图纸会保留在这里。' : '点击顶部「导入图片」「导入 OXS」或「新建图纸」开始。'}</p>
      </div> : <div className="project-cards">
        {result.items.map((project) => <article className="project-card" key={project.project_id} aria-label={project.name}>
          <button className="project-preview" type="button" disabled={busy || deleted || !project.head_revision_id}
            aria-label={`打开 ${project.name}`} onClick={() => void action(() => onOpen(project.project_id))}>
            {project.head_revision_id && <img loading="lazy" alt={`${project.name}缩略图`}
              src={`/api/v1/projects/${encodeURIComponent(project.project_id)}/thumbnail?revision_id=${encodeURIComponent(project.head_revision_id)}`} />}
          </button>
          <div className="project-card-body">
            <div className="project-card-title"><h2 title={project.name}>{project.name}</h2><span>{({ image: '图片生成', oxs: 'OXS 导入', blank: '新建' })[project.source_kind]}</span></div>
            <p>{project.width ?? '—'} × {project.height ?? '—'} 格 · {project.fabric_count ?? '—'} CT · {project.color_count ?? 0} 色</p>
            <time dateTime={new Date(project.updated_at_ms).toISOString()}>更新于 {new Date(project.updated_at_ms).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })}</time>
            <div className="project-card-actions">
              {deleted ? <button className="quiet-button" type="button" disabled={busy} onClick={() => void action(async () => {
                await restoreProject(project.project_id); refresh()
              })}>恢复图纸</button> : <>
                <button className="primary-button" type="button" disabled={busy || !project.head_revision_id}
                  onClick={() => void action(() => onOpen(project.project_id))}>打开图纸</button>
                <button className="quiet-button" type="button" disabled={busy || !project.head_revision_id} onClick={() => { setRenaming(project); setName(project.name) }}>重命名</button>
                <button className="quiet-button" type="button" disabled={busy} onClick={() => {
                  if (!window.confirm(`将「${project.name}」移入回收站？之后可以恢复。`)) return
                  void action(async () => { await deleteProject(project.project_id); onRemoved(project.project_id); refresh() })
                }}>移入回收站</button>
              </>}
            </div>
          </div>
        </article>)}
      </div>}
      {result.total > 24 && <nav className="library-pagination" aria-label="图纸分页">
        <button type="button" className="quiet-button" disabled={busy || offset === 0} onClick={() => { setOffset(offset - 24); setLoading(true) }}>上一页</button>
        <span>第 {offset / 24 + 1} / {Math.ceil(result.total / 24)} 页</span>
        <button type="button" className="quiet-button" disabled={busy || offset + 24 >= result.total} onClick={() => { setOffset(offset + 24); setLoading(true) }}>下一页</button>
      </nav>}
    </>}
    {renaming && <div className="import-backdrop"><section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-title">
      <h2 id="rename-title">重命名图纸</h2>
      <form onSubmit={(event) => { event.preventDefault(); void action(async () => {
        const revision = await renameProject(renaming.project_id, name.trim(), renaming.head_revision_id!)
        onRenamed(renaming.project_id, name.trim(), revision.revision_id)
        setRenaming(null); refresh()
      }) }}>
        <label className="rename-field">图纸名称<input autoFocus maxLength={256} required value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
        {error && <p className="library-error" role="alert">{error}</p>}
        <div className="import-actions"><button type="button" disabled={busy} onClick={() => setRenaming(null)}>取消</button>
          <button className="primary-button" type="submit" disabled={busy || !name.trim()}>{busy ? '保存中…' : '保存名称'}</button></div>
      </form>
    </section></div>}
  </section>
}
