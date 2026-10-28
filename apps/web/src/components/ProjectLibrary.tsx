import { useEffect, useRef, useState } from 'react'
import { deleteProject, listProjects, renameProject, restoreProject } from '../lib/api'
import type { LibraryProject } from '../lib/types'
import { SelectControl } from './SelectControl'
import {
  ArrowsClockwiseIcon,
  ClockIcon,
  FileCodeIcon,
  EyeIcon,
  FolderOpenIcon,
  GridFourIcon,
  HashIcon,
  ImageBrokenIcon,
  ListIcon,
  LockSimpleIcon,
  MagicWandIcon,
  MagnifyingGlassIcon,
  PaletteIcon,
  PencilSimpleIcon,
  PlusIcon,
  SortAscendingIcon,
  TrashIcon,
  XIcon,
} from '@phosphor-icons/react'

interface Props {
  onOpen: (id: string) => Promise<void>
  onRemoved: (id: string) => void
  onRenamed: (id: string, name: string, head: string, previousHead: string) => void
  onBusy: (busy: boolean) => void
  onImportImage: () => void
  onImportPattern: () => void
  onCreateBlank: () => void
  inert?: boolean
}

export function ProjectLibrary({ onOpen, onRemoved, onRenamed, onBusy, onImportImage, onImportPattern, onCreateBlank, inert }: Props) {
  const [search, setSearch] = useState('')
  const [deleted, setDeleted] = useState(false)
  const [sort, setSort] = useState('updated')
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const [selectedId, setSelectedId] = useState<string | null>(null)
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
        setError(null)
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
    image: { label: '图片生成', shortLabel: '图片', icon: MagicWandIcon, className: 'kind-image' },
    oxs: { label: 'OXS 导入', shortLabel: 'OXS', icon: FileCodeIcon, className: 'kind-oxs' },
    xsd: { label: 'XSD 导入', shortLabel: 'XSD', icon: FileCodeIcon, className: 'kind-oxs' },
    blank: { label: '空白图纸', shortLabel: '空白', icon: PlusIcon, className: 'kind-blank' },
  }
  const selectedProject = result.items.find((project) => project.project_id === selectedId) ?? result.items[0] ?? null
  const selectedThumbnailFailed = selectedProject ? failedThumbnails.has(selectedProject.project_id) : false

  function thumbnailUrl(project: LibraryProject) {
    if (!project.head_revision_id) return ''
    return `/api/v1/projects/${encodeURIComponent(project.project_id)}/thumbnail?revision_id=${encodeURIComponent(project.head_revision_id)}`
  }

  return (
    <section className="project-library" aria-label="图纸库" inert={inert}>
      <div className="library-header-wrapper">
        <div className="library-heading">
          <div>
            <h1>我的图纸</h1>
            <p>打开图纸查线色，或创建下一件作品。</p>
          </div>
          <span className="storage-badge"><LockSimpleIcon size={14} aria-hidden="true" />仅自己可见</span>
          <div className="library-view-switcher" aria-label="图纸显示方式">
            <button type="button" className={view === 'grid' ? 'active' : ''} aria-pressed={view === 'grid'} onClick={() => setView('grid')}>
              <GridFourIcon size={17} weight={view === 'grid' ? 'fill' : 'regular'} aria-hidden="true" />网格
            </button>
            <button type="button" className={view === 'list' ? 'active' : ''} aria-pressed={view === 'list'} onClick={() => setView('list')}>
              <ListIcon size={17} weight={view === 'list' ? 'bold' : 'regular'} aria-hidden="true" />列表
            </button>
          </div>
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
            <MagnifyingGlassIcon className="search-box-icon" aria-hidden="true" />
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
              <SortAscendingIcon className="select-icon" aria-hidden="true" />
              <SelectControl
                label="图纸排序"
                value={sort}
                disabled={busy}
                onChange={(value) => {
                  setSort(value); setOffset(0); setLoading(true); setError(null)
                }}
              >
                <option value="updated">最近更新</option>
                <option value="created">最近创建</option>
                <option value="name">名称排序</option>
              </SelectControl>
            </div>

            <button type="button" className="quiet-button refresh-button" aria-label="刷新图纸列表" disabled={busy} onClick={refresh}>
              <ArrowsClockwiseIcon aria-hidden="true" />
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
          <div className="library-content" aria-hidden="true">
          <div className="project-cards">
            {Array.from({ length: 6 }, (_, index) => <div className="project-card skeleton-card" key={index}>
              <div className="skeleton-media" />
              <div className="skeleton-body"><i /><i /><i /></div>
            </div>)}
          </div>
          <div className="project-detail skeleton-detail"><div className="skeleton-media" /><div className="skeleton-body"><i /><i /><i /></div></div>
          </div>
        </div>
      ) : (
        <>
          <div className="library-summary">
            <span>{deleted ? '回收站' : '图纸'} {result.total} 份{deleted && '，可随时恢复，原图和历史版本仍保留'}</span>
            <span className="library-private-note">点击预览查看详情，或直接打开图纸</span>
          </div>

          {result.items.length === 0 ? (
            <div className="library-empty">
              <div className="empty-icon-wrap">
                {search ? <MagnifyingGlassIcon className="empty-icon" /> : <FolderOpenIcon className="empty-icon" />}
              </div>
              <h2>{search ? '没有找到匹配的图纸' : deleted ? '回收站是空的' : '还没有图纸'}</h2>
              <p>{search ? `没有名称包含「${search}」的图纸，试试其他关键词。` : deleted ? '移入回收站的图纸会保留在这里，可随时恢复。' : '上传喜欢的图片，或打开已有图纸，开始你的第一件作品。'}</p>
              <div className="library-empty-actions">
                {search ? <button className="quiet-button" type="button" onClick={() => { setSearch(''); setOffset(0); setLoading(true) }}>清空搜索</button>
                  : deleted ? <button className="quiet-button" type="button" onClick={() => { setDeleted(false); setOffset(0); setLoading(true) }}>返回全部图纸</button>
                  : <>
                    <button className="primary-button" type="button" onClick={onImportImage}><MagicWandIcon size={18} aria-hidden="true" />图片转图纸</button>
                    <button className="quiet-button" type="button" onClick={onImportPattern}><FileCodeIcon size={18} aria-hidden="true" />导入 OXS / XSD</button>
                    <button className="empty-blank-link" type="button" onClick={onCreateBlank}><PlusIcon size={15} aria-hidden="true" />从空白图纸开始</button>
                  </>}
              </div>
              {!search && !deleted && <small className="empty-cost-note">导入与空白绘制免费 · 图片生成成功消耗 1 次</small>}
            </div>
          ) : (
            <div className={`library-content view-${view}`}>
            <div className={`project-cards view-${view}`}>
              {result.items.map((project) => {
                const kindMeta = sourceKindMap[project.source_kind] ?? sourceKindMap.blank
                const KindIcon = kindMeta.icon
                return (
                  <article className={`project-card${selectedProject?.project_id === project.project_id ? ' selected' : ''}`} key={project.project_id} aria-label={project.name}>
                    <button
                      className="project-select-button"
                      type="button"
                      aria-label={`查看 ${project.name} 详情`}
                      aria-pressed={selectedProject?.project_id === project.project_id}
                      onClick={() => setSelectedId(project.project_id)}
                      onDoubleClick={() => { if (!deleted && project.head_revision_id) void action(() => onOpen(project.project_id)) }}
                    >
                      <div className="project-card-media">
                          {project.head_revision_id && !failedThumbnails.has(project.project_id) ? (
                            <img
                              loading="lazy"
                              alt=""
                              src={thumbnailUrl(project)}
                              onError={() => setFailedThumbnails((current) => new Set(current).add(project.project_id))}
                            />
                          ) : (
                            <div className="preview-placeholder"><ImageBrokenIcon size={24} aria-hidden="true" /><span>预览暂不可用</span></div>
                          )}
                      </div>
                      <div className="project-card-body">
                      <div className="project-card-title">
                        <h2 title={project.name}>{project.name}</h2>
                        <span className={`project-kind-badge ${kindMeta.className}`} title={kindMeta.label}>
                          <KindIcon aria-hidden="true" />
                          <span>{kindMeta.shortLabel}</span>
                        </span>
                      </div>

                      <p className="project-meta-line">
                        <span>{project.width ?? '-'} × {project.height ?? '-'}</span>
                        <span>{project.fabric_count ?? '-'} CT</span>
                        <span>{project.color_count ?? 0} 色</span>
                      </p>

                      <time dateTime={new Date(project.updated_at_ms).toISOString()}>
                        更新于 {new Date(project.updated_at_ms).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })}
                      </time>
                      </div>
                    </button>
                    {!deleted && <button className="project-quick-open" type="button" disabled={busy || !project.head_revision_id}
                      aria-label={`打开图纸 ${project.name}`} onClick={() => void action(() => onOpen(project.project_id))}>
                      <EyeIcon size={16} aria-hidden="true" />打开图纸
                    </button>}
                  </article>
                )
              })}
            </div>
            {selectedProject && (
              <aside className="project-detail" aria-label={`${selectedProject.name} 详情`} aria-live="polite">
                <div className="project-detail-media">
                  {selectedProject.head_revision_id && !selectedThumbnailFailed ? (
                    <img
                      src={thumbnailUrl(selectedProject)}
                      alt={`${selectedProject.name} 图纸预览`}
                      onError={() => setFailedThumbnails((current) => new Set(current).add(selectedProject.project_id))}
                    />
                  ) : (
                    <div className="preview-placeholder"><ImageBrokenIcon size={30} aria-hidden="true" /><span>预览暂不可用</span></div>
                  )}
                </div>
                <div className="project-detail-heading">
                  <div><h2>{selectedProject.name}</h2><span>{sourceKindMap[selectedProject.source_kind].label}</span></div>
                  {!deleted && <div className="project-detail-actions">
                    <button type="button" aria-label={`重命名 ${selectedProject.name}`} title="重命名" disabled={busy || !selectedProject.head_revision_id} onClick={() => { setRenaming(selectedProject); setName(selectedProject.name) }}>
                      <PencilSimpleIcon size={18} aria-hidden="true" />
                    </button>
                    <button type="button" aria-label={`将 ${selectedProject.name} 移入回收站`} title="移入回收站" disabled={busy} onClick={() => {
                      if (!window.confirm(`将「${selectedProject.name}」移入回收站？之后可以恢复。`)) return
                      void action(async () => {
                        await deleteProject(selectedProject.project_id)
                        onRemoved(selectedProject.project_id)
                        setSelectedId(null)
                        refresh()
                      })
                    }}>
                      <TrashIcon size={18} aria-hidden="true" />
                    </button>
                  </div>}
                </div>

                {deleted ? (
                  <button className="primary-button project-continue" type="button" disabled={busy} onClick={() => void action(async () => {
                    await restoreProject(selectedProject.project_id)
                    setSelectedId(null)
                    refresh()
                  })}>
                    <ArrowsClockwiseIcon size={18} aria-hidden="true" />恢复图纸
                  </button>
                ) : (
                  <div className="project-open-block">
                    <button className="primary-button project-continue" type="button" disabled={busy || !selectedProject.head_revision_id} onClick={() => void action(() => onOpen(selectedProject.project_id))}>
                      <EyeIcon size={19} weight="bold" aria-hidden="true" />打开图纸
                    </button>
                    <p>缩放查看针格 · 高亮同色 · 随时切换编辑</p>
                  </div>
                )}

                <div className="project-detail-section">
                  <h3>图纸信息</h3>
                  <dl>
                    <div><dt><GridFourIcon size={17} aria-hidden="true" />图纸尺寸</dt><dd>{selectedProject.width ?? '-'} × {selectedProject.height ?? '-'}</dd></div>
                    <div><dt><HashIcon size={17} aria-hidden="true" />布料规格</dt><dd>{selectedProject.fabric_count ?? '-'} CT</dd></div>
                    <div><dt><PaletteIcon size={17} aria-hidden="true" />颜色数量</dt><dd>{selectedProject.color_count ?? 0} 色</dd></div>
                    <div><dt><ClockIcon size={17} aria-hidden="true" />最后编辑</dt><dd>{new Date(selectedProject.updated_at_ms).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}</dd></div>
                  </dl>
                </div>

                <div className="project-privacy-note"><LockSimpleIcon size={18} aria-hidden="true" /><div><strong>图纸仅自己可见</strong><p>原图、图纸和版本记录都保存在你的账号下。</p></div></div>
              </aside>
            )}
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
              <button type="button" aria-label="关闭" onClick={() => setRenaming(null)}><XIcon size={20} aria-hidden="true" /></button>
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
