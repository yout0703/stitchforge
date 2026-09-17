import { useEffect, useRef, useState } from 'react'
import { deleteProject, listProjects, renameProject, restoreProject } from '../lib/api'
import type { LibraryProject } from '../lib/types'
import {
  IconSearch,
  IconSparkles,
  IconFileCode,
  IconPlus,
  IconSort,
  IconRefresh,
  IconTrash,
  IconEdit,
  IconFolder,
} from './Icons'

interface Props {
  onOpen: (id: string) => Promise<void>
  onRemoved: (id: string) => void
  onRenamed: (id: string, name: string, head: string, previousHead: string) => void
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
  const [failedThumbnails, setFailedThumbnails] = useState<Set<string>>(() => new Set())
  const [renaming, setRenaming] = useState<LibraryProject | null>(null)
  const [name, setName] = useState('')
  const renameDialogRef = useRef<HTMLElement>(null)
  const busyRef = useRef(false)

  useEffect(() => { busyRef.current = busy }, [busy])
  useEffect(() => {
    if (!renaming) return
    const previousFocus = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
    const dialog = renameDialogRef.current
    dialog?.querySelector<HTMLInputElement>('input')?.focus()
    function keepFocusInside(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        if (!busyRef.current) setRenaming(null)
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)')]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && window.document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && window.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.document.addEventListener('keydown', keepFocusInside)
    return () => { window.document.removeEventListener('keydown', keepFocusInside); previousFocus?.focus() }
  }, [renaming])

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

  const sourceKindMap = {
    image: { label: '图片生成', icon: IconSparkles, className: 'kind-image' },
    oxs: { label: 'OXS 导入', icon: IconFileCode, className: 'kind-oxs' },
    blank: { label: '新建', icon: IconPlus, className: 'kind-blank' },
  }

  return (
    <section className="project-library" aria-label="图纸库">
      <div className="library-header-wrapper">
        <div className="library-heading">
          <div>
            <h1>我的图纸</h1>
            <p>新建、图片生成和 OXS 导入的作品，都保存在这里。</p>
          </div>
          <span className="storage-badge">仅自己可见</span>
        </div>

        <div className="library-controls">
          <div className="view-tabs">
            <button
              type="button"
              className={!deleted ? 'active' : ''}
              aria-pressed={!deleted}
              disabled={busy}
              onClick={() => { if (!deleted) return; setDeleted(false); setOffset(0); setLoading(true); setError(null) }}
            >
              全部图纸
            </button>
            <button
              type="button"
              className={deleted ? 'active' : ''}
              aria-pressed={deleted}
              disabled={busy}
              onClick={() => { if (deleted) return; setDeleted(true); setOffset(0); setLoading(true); setError(null) }}
            >
              回收站
            </button>
          </div>

          <div className="search-box-wrapper">
            <IconSearch className="search-box-icon" aria-hidden="true" />
            <input
              type="search"
              aria-label="搜索图纸"
              placeholder="搜索图纸名称"
              maxLength={256}
              value={search}
              disabled={busy}
              onChange={(event) => { setSearch(event.target.value); setOffset(0); setLoading(true); setError(null) }}
            />
          </div>

          <div className="library-actions-group">
            <div className="select-wrapper">
              <IconSort className="select-icon" aria-hidden="true" />
              <select
                aria-label="图纸排序"
                value={sort}
                disabled={busy}
                onChange={(event) => {
                  setSort(event.target.value); setOffset(0); setLoading(true); setError(null)
                }}
              >
                <option value="updated">最近更新</option>
                <option value="created">最近创建</option>
                <option value="name">名称排序</option>
              </select>
            </div>

            <button type="button" className="quiet-button refresh-button" aria-label="刷新图纸列表" disabled={busy} onClick={refresh}>
              <IconRefresh aria-hidden="true" />
              <span>刷新列表</span>
            </button>
          </div>
        </div>
      </div>

      {error ? (
        <div className="library-error library-error-panel" role="alert">
          <div><strong>暂时无法读取图纸</strong><p>{error}</p></div>
          <button type="button" className="quiet-button" onClick={refresh}>重新加载</button>
        </div>
      ) : loading ? (
        <div className="library-loading" role="status">
          <span className="sr-only">正在读取图纸…</span>
          <div className="project-cards" aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => <div className="project-card skeleton-card" key={index}>
              <div className="skeleton-media" />
              <div className="skeleton-body"><i /><i /><i /></div>
            </div>)}
          </div>
        </div>
      ) : (
        <>
          <div className="library-summary">
            <span>{deleted ? '回收站' : '图纸'} · {result.total} 份{deleted && ' · 可随时恢复，原图和历史版本仍保留'}</span>
            <span className="library-private-note">仅自己可见</span>
          </div>

          {result.items.length === 0 ? (
            <div className="library-empty">
              <div className="empty-icon-wrap">
                <IconFolder className="empty-icon" />
              </div>
              <h2>{search ? '没有找到匹配的图纸' : deleted ? '回收站是空的' : '还没有图纸'}</h2>
              <p>{search ? '换个名称试试，或清空搜索。' : deleted ? '移除的图纸会保留在这里。' : '点击顶部「导入图片」「导入 OXS」或「新建图纸」开始。'}</p>
            </div>
          ) : (
            <div className="project-cards">
              {result.items.map((project) => {
                const kindMeta = sourceKindMap[project.source_kind] ?? sourceKindMap.blank
                const KindIcon = kindMeta.icon
                return (
                  <article className="project-card" key={project.project_id} aria-label={project.name}>
                    <div className="project-card-media">
                      <button
                        className="project-preview"
                        type="button"
                        disabled={busy || deleted || !project.head_revision_id}
                        aria-label={`打开 ${project.name}`}
                        onClick={() => void action(() => onOpen(project.project_id))}
                      >
                        {project.head_revision_id && !failedThumbnails.has(project.project_id) ? (
                          <img
                            loading="lazy"
                            alt={`${project.name}缩略图`}
                            src={`/api/v1/projects/${encodeURIComponent(project.project_id)}/thumbnail?revision_id=${encodeURIComponent(project.head_revision_id)}`}
                            onError={() => setFailedThumbnails((current) => new Set(current).add(project.project_id))}
                          />
                        ) : (
                          <div className="preview-placeholder">预览暂不可用</div>
                        )}
                      </button>
                      <span className={`project-kind-badge ${kindMeta.className}`}>
                        <KindIcon aria-hidden="true" />
                        <span>{kindMeta.label}</span>
                      </span>
                    </div>

                    <div className="project-card-body">
                      <div className="project-card-title">
                        <h2 title={project.name}>{project.name}</h2>
                      </div>

                      <p className="project-meta-line">
                        {project.width ?? '—'} × {project.height ?? '—'} 格 · {project.fabric_count ?? '—'} CT · {project.color_count ?? 0} 色
                      </p>

                      <time dateTime={new Date(project.updated_at_ms).toISOString()}>
                        更新于 {new Date(project.updated_at_ms).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })}
                      </time>

                      <div className="project-card-actions">
                        {deleted ? (
                          <button
                            className="quiet-button restore-btn"
                            type="button"
                            disabled={busy}
                            onClick={() => void action(async () => {
                              await restoreProject(project.project_id)
                              refresh()
                            })}
                          >
                            <IconRefresh aria-hidden="true" />
                            <span>恢复图纸</span>
                          </button>
                        ) : (
                          <>
                            <button
                              className="primary-button open-btn"
                              type="button"
                              disabled={busy || !project.head_revision_id}
                              onClick={() => void action(() => onOpen(project.project_id))}
                            >
                              打开图纸
                            </button>
                            <button
                              className="quiet-button edit-btn"
                              type="button"
                              aria-label={`重命名 ${project.name}`}
                              title="重命名"
                              disabled={busy || !project.head_revision_id}
                              onClick={() => { setRenaming(project); setName(project.name) }}
                            >
                              <IconEdit aria-hidden="true" />
                              <span>重命名</span>
                            </button>
                            <button
                              className="quiet-button delete-btn"
                              type="button"
                              aria-label={`将 ${project.name} 移入回收站`}
                              title="移入回收站"
                              disabled={busy}
                              onClick={() => {
                                if (!window.confirm(`将「${project.name}」移入回收站？之后可以恢复。`)) return
                                void action(async () => {
                                  await deleteProject(project.project_id)
                                  onRemoved(project.project_id)
                                  refresh()
                                })
                              }}
                            >
                              <IconTrash aria-hidden="true" />
                              <span>移入回收站</span>
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}

          {result.total > 24 && (
            <nav className="library-pagination" aria-label="图纸分页">
              <button
                type="button"
                className="quiet-button"
                disabled={busy || offset === 0}
                onClick={() => { setOffset(offset - 24); setLoading(true) }}
              >
                上一页
              </button>
              <span className="pagination-text">第 {offset / 24 + 1} / {Math.ceil(result.total / 24)} 页</span>
              <button
                type="button"
                className="quiet-button"
                disabled={busy || offset + 24 >= result.total}
                onClick={() => { setOffset(offset + 24); setLoading(true) }}
              >
                下一页
              </button>
            </nav>
          )}
        </>
      )}

      {renaming && (
        <div className="import-backdrop">
          <section ref={renameDialogRef} className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-title">
            <div className="import-heading">
              <div>
                <h2 id="rename-title">重命名图纸</h2>
                <p>为你的刺绣作品设置一个清晰易记的名字。</p>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setRenaming(null)}>×</button>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void action(async () => {
                  const revision = await renameProject(renaming.project_id, name.trim(), renaming.head_revision_id!)
                  onRenamed(renaming.project_id, name.trim(), revision.revision_id, renaming.head_revision_id!)
                  setRenaming(null)
                  refresh()
                })
              }}
            >
              <label className="rename-field">
                <span>图纸名称</span>
                <input
                  autoFocus
                  maxLength={256}
                  required
                  value={name}
                  disabled={busy}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              {error && <p className="library-error" role="alert">{error}</p>}
              <div className="import-actions">
                <button type="button" disabled={busy} onClick={() => setRenaming(null)}>取消</button>
                <button className="primary-button" type="submit" disabled={busy || !name.trim()}>
                  {busy ? '保存中…' : '保存名称'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  )
}
