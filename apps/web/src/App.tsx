import { useEffect, useMemo, useReducer, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import { ProjectLibrary } from './components/ProjectLibrary'
import { PatternCanvas } from './components/PatternCanvas'
import {
  convertProjectImage,
  deleteProject,
  createProject,
  createRevision,
  getProject,
  getRevision,
  importOxsProject,
} from './lib/api'
import { createEditor, editorReducer, isDirty } from './lib/editor'
import { makeDemoPattern } from './lib/demoPattern'
import { recipeSymbols } from './lib/symbols'
import { selectionIndices } from './lib/selection'
import type {
  CellSelection,
  ImageConversionResponse,
  PatternDocument,
  Tool,
} from './lib/types'

const TOOLS: { id: Tool; label: string; shortcut: string }[] = [
  { id: 'paint', label: '画笔', shortcut: 'B' },
  { id: 'pick', label: '取色', shortcut: 'I' },
  { id: 'select', label: '选区', shortcut: 'S' },
  { id: 'pan', label: '移动', shortcut: 'H' },
  { id: 'erase', label: '橡皮擦', shortcut: 'E' },
  { id: 'fill', label: '填色', shortcut: 'G' },
]

const LAST_PROJECT_KEY = 'stitchforge.lastProjectId'

function sourceUrlFromDocument(document: PatternDocument): string | null {
  const provenance = document.provenance as
    | { sources?: { id?: string; locator?: string }[] }
    | undefined
  const locator = provenance?.sources?.find((source) => source.id === 'src_image')?.locator
  return locator?.startsWith('/api/v1/assets/') ? locator : null
}

function App() {
  const [editor, dispatch] = useReducer(editorReducer, undefined, () => createEditor(makeDemoPattern()))
  const { document } = editor
  const dirty = isDirty(editor)
  const [libraryOpen, setLibraryOpen] = useState(() => window.location.hash === '#library' || !window.localStorage.getItem(LAST_PROJECT_KEY))
  const [libraryBusy, setLibraryBusy] = useState(false)
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  const [highlightedRecipe, setHighlightedRecipe] = useState<number | null>(null)
  const [showSymbols, setShowSymbols] = useState(false)
  const [showGrid, setShowGrid] = useState(true)
  const [tool, setTool] = useState<Tool>('paint')
  const [selectedRecipe, setSelectedRecipe] = useState(1)
  const [hoveredCell, setHoveredCell] = useState<{ col: number; row: number } | null>(null)
  const [selection, setSelection] = useState<CellSelection | null>(null)
  const [createState, setCreateState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [saveState, setSaveState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [session, setSession] = useState<{ projectId: string; headRevisionId: string } | null>(null)
  const [viewMode, setViewMode] = useState<'pattern' | 'source'>('pattern')
  const [sourceFullView, setSourceFullView] = useState(true)
  const [fitRequest, setFitRequest] = useState(0)
  const [sourceImageUrl, setSourceImageUrl] = useState<string | null>(null)
  const [importKind, setImportKind] = useState<'image' | 'oxs'>('image')
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importState, setImportState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [importError, setImportError] = useState<string | null>(null)
  const [conversionSummary, setConversionSummary] = useState<ImageConversionResponse | null>(null)
  const [restoreState, setRestoreState] = useState<'idle' | 'loading' | 'error'>(() =>
    window.localStorage.getItem(LAST_PROJECT_KEY) ? 'loading' : 'idle',
  )

  useEffect(() => {
    window.history.replaceState(null, '', libraryOpen ? '#library' : '#pattern')
  }, [libraryOpen])

  const palette = useMemo(() => {
    const materials = new Map(document.materials.map((item) => [item.id, item]))
    return document.recipes.map((recipe) => ({
      ...recipe,
      material: materials.get(recipe.components[0]?.material_id ?? ''),
    }))
  }, [document.materials, document.recipes])

  const symbols = useMemo(() => recipeSymbols(document), [document])
  const recipeCounts = useMemo(() => {
    const counts = new Map<number, number>()
    for (const recipe of document.full_stitches.cells) {
      if (recipe > 0) counts.set(recipe, (counts.get(recipe) ?? 0) + 1)
    }
    return counts
  }, [document.full_stitches.cells])

  const hoveredRecipeIndex = hoveredCell ? document.full_stitches.cells[hoveredCell.row * document.grid.width + hoveredCell.col] : null
  const hoveredMaterial = palette.find((recipe) => recipe.index === hoveredRecipeIndex)?.material
  const planningReport = conversionSummary?.planning_report ??
    (document.extensions?.['stitchforge.import_plan'] as ImageConversionResponse['planning_report'] | undefined)
  const embeddedOxsPalette = (document.provenance as { catalog_snapshot_id?: string } | undefined)?.catalog_snapshot_id === 'oxs_embedded_palette'

  useEffect(() => {
    if (highlightedRecipe === null || mode !== 'view') return
    window.document.querySelector(`[data-recipe-index="${highlightedRecipe}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [highlightedRecipe, mode])

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const target = event.target
      if (libraryOpen || importOpen || target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement || (target instanceof HTMLElement && target.isContentEditable)) return
      if (event.altKey) return
      const key = event.key.toLowerCase()
      if (mode === 'view') {
        if (key === 'escape') setHighlightedRecipe(null)
        if ((event.metaKey || event.ctrlKey) && ['s', 'z', 'y'].includes(key)) event.preventDefault()
        return
      }
      if (event.metaKey || event.ctrlKey) {
        if (key === 'z' || key === 'y') {
          event.preventDefault()
          if (!editor.active) dispatch({ type: key === 'y' || event.shiftKey ? 'redo' : 'undo' })
        } else if (key === 's') {
          event.preventDefault()
          void saveRevision()
        } else if (key === 'a') {
          event.preventDefault()
          setSelection({ startCol: 0, startRow: 0, endCol: document.grid.width - 1, endRow: document.grid.height - 1 })
        }
        return
      }
      const nextTool = { b: 'paint', i: 'pick', s: 'select', h: 'pan', e: 'erase', g: 'fill' }[key] as Tool | undefined
      if (nextTool && !editor.active) setTool(nextTool)
      if (key === 'escape') setSelection(null)
      if ((key === 'delete' || key === 'backspace') && selection && !editor.active) {
        event.preventDefault()
        paintCells(selectionIndices(selection, document.grid.width), 0, '清空选区')
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  })

  useEffect(() => {
    if (!dirty) return
    function preventLoss(event: BeforeUnloadEvent) {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventLoss)
    return () => window.removeEventListener('beforeunload', preventLoss)
  }, [dirty])

  useEffect(() => {
    const projectId = window.localStorage.getItem(LAST_PROJECT_KEY)
    if (!projectId) return
    let cancelled = false
    async function restore() {
      try {
        const project = await getProject(projectId as string)
        if (!project.head_revision_id || project.deleted_at_ms) throw new Error('图纸已移入回收站或没有可打开版本')
        const revision = await getRevision(project.project_id, project.head_revision_id)
        if (cancelled) return
        dispatch({ type: 'reset', document: revision.document })
        setHighlightedRecipe(null)
        setSession({
          projectId: project.project_id,
          headRevisionId: revision.revision_id,
        })
        setSourceImageUrl(sourceUrlFromDocument(revision.document))
        setSelectedRecipe(revision.document.recipes[0]?.index ?? 0)
        setRestoreState('idle')
      } catch {
        if (cancelled) return
        window.localStorage.removeItem(LAST_PROJECT_KEY)
        setRestoreState('error')
        setLibraryOpen(true)
      }
    }
    void restore()
    return () => {
      cancelled = true
    }
  }, [])

  function paintCells(indices: readonly number[], recipeIndex: number, label = '画笔') {
    dispatch({ type: 'paint', indices, recipe: recipeIndex, label })
  }

  function pickCell(index: number) {
    const recipe = document.full_stitches.cells[index]
    if (mode === 'view') {
      setHighlightedRecipe(recipe > 0 ? recipe : null)
      return
    }
    if (recipe > 0) {
      setSelectedRecipe(recipe)
      setTool('paint')
    }
  }

  function showLibrary() {
    if (dirty && !window.confirm('当前图纸有未保存修改。确定放弃修改并返回图纸库？')) return
    if (dirty) dispatch({ type: 'reset', document: editor.savedDocument })
    setLibraryOpen(true)
  }

  async function openLibraryProject(projectId: string) {
    const project = await getProject(projectId)
    if (!project.head_revision_id || project.deleted_at_ms) throw new Error('图纸已移入回收站或没有可打开版本')
    const revision = await getRevision(projectId, project.head_revision_id)
    dispatch({ type: 'reset', document: revision.document })
    setSession({ projectId, headRevisionId: revision.revision_id })
    window.localStorage.setItem(LAST_PROJECT_KEY, projectId)
    setSourceImageUrl(sourceUrlFromDocument(revision.document))
    setSelectedRecipe(revision.document.recipes[0]?.index ?? 0)
    setSelection(null)
    setHoveredCell(null)
    setHighlightedRecipe(null)
    setConversionSummary(null)
    setImportNotice(null)
    setRestoreState('idle')
    setSaveState('idle')
    setViewMode('pattern')
    setSourceFullView(true)
    setMode('view')
    setFitRequest((value) => value + 1)
    setLibraryOpen(false)
  }

  async function startBlankProject() {
    if (dirty && !window.confirm('当前图纸有未保存修改。确定放弃修改并新建图纸？')) return
    setCreateState('loading')
    try {
      const created = await createProject('未命名图纸')
      dispatch({ type: 'reset', document: created.initial_revision.document })
      setHighlightedRecipe(null)
      setSelectedRecipe(0)
      setSelection(null)
      setSourceImageUrl(null)
      setImportNotice(null)
      setSourceFullView(true)
      setConversionSummary(null)
      setViewMode('pattern')
      setSession({
        projectId: created.project.project_id,
        headRevisionId: created.initial_revision.revision_id,
      })
      window.localStorage.setItem(LAST_PROJECT_KEY, created.project.project_id)
      setMode('view')
      setHoveredCell(null)
      setFitRequest((value) => value + 1)
      setLibraryOpen(false)
      setCreateState('idle')
    } catch {
      setCreateState('error')
    }
  }

  async function importImage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const file = form.get('image')
    if (!(file instanceof File) || file.size === 0) {
      setImportState('error')
      setImportError(importKind === 'oxs' ? '请选择 OXS 文件' : '请选择一张 JPG 或 PNG 图片')
      return
    }
    if (dirty && !window.confirm('生成新图纸会替换当前未保存修改。确定继续？')) return
    setImportState('loading')
    setImportError(null)
    let pendingProjectId: string | null = null
    try {
      if (importKind === 'oxs') {
        if (file.size > 16 * 1024 * 1024) throw new Error('OXS 文件超过 16 MiB 限制')
        const imported = await importOxsProject(file)
        dispatch({ type: 'reset', document: imported.initial_revision.document })
        setSession({ projectId: imported.project.project_id, headRevisionId: imported.initial_revision.revision_id })
        window.localStorage.setItem(LAST_PROJECT_KEY, imported.project.project_id)
        setSelectedRecipe(imported.initial_revision.document.recipes[0]?.index ?? 0)
        setSourceImageUrl(null)
        setConversionSummary(null)
        setImportNotice(imported.report.issues.map((issue) => issue.message).join('；') || null)
        setHighlightedRecipe(null)
        setSelection(null)
        setViewMode('pattern')
        setMode('view')
        setFitRequest((current) => current + 1)
        setLibraryOpen(false)
        setHoveredCell(null)
        setImportOpen(false)
        setImportState('idle')
        return
      }
      if (file.size > 20 * 1024 * 1024) throw new Error('图片文件超过 20 MiB 限制')
      const title = file.name.replace(/\.[^.]+$/, '') || '图片图纸'
      const created = await createProject(title)
      pendingProjectId = created.project.project_id
      const activeSession = {
        projectId: created.project.project_id,
        headRevisionId: created.initial_revision.revision_id,
      }
      const result = await convertProjectImage(activeSession.projectId, file, {
        expectedHeadRevisionId: activeSession.headRevisionId,
        automatic: true,
      })
      pendingProjectId = null
      setLibraryOpen(false)
      setHoveredCell(null)
      dispatch({ type: 'reset', document: result.revision.document })
      setHighlightedRecipe(null)
      setSession({
        projectId: activeSession.projectId,
        headRevisionId: result.revision.revision_id,
      })
      window.localStorage.setItem(LAST_PROJECT_KEY, activeSession.projectId)
      setMode('view')
      setSourceFullView(true)
      setFitRequest((current) => current + 1)
      setImportNotice(null)
      setSourceImageUrl(result.source_asset.url)
      setConversionSummary(result)
      setSelectedRecipe(result.revision.document.recipes[0]?.index ?? 0)
      setSelection(null)
      setViewMode('pattern')
      setImportOpen(false)
      setImportState('idle')
    } catch (error) {
      if (pendingProjectId) {
        try { await deleteProject(pendingProjectId) } catch { /* The empty project remains available in the library. */ }
      }
      setImportState('error')
      setImportError(error instanceof Error ? error.message : '导入失败')
    }
  }

  async function saveRevision() {
    if (!session || !dirty || editor.active || saveState === 'loading') return
    setSaveState('loading')
    try {
      const revision = await createRevision(session.projectId, session.headRevisionId, document)
      setSession({ ...session, headRevisionId: revision.revision_id })
      window.localStorage.setItem(LAST_PROJECT_KEY, session.projectId)
      dispatch({ type: 'saved', document })
      setSaveState('idle')
    } catch {
      setSaveState('error')
    }
  }

  return (
    <main className="workbench-shell">
      <header className="app-header">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">SF</span>
          <div>
            <strong>StitchForge</strong>
            <span>十字绣打版工作台</span>
          </div>
        </div>
        <div className="document-title">
          <strong>{libraryOpen ? '图纸库' : document.metadata.title}</strong>
          <span>{libraryOpen ? '管理本机的所有作品' : dirty ? '有未保存修改' : session ? '已保存到本机' : '示例预览'}</span>
        </div>
        <div className="header-actions">
          {!libraryOpen && <button className="quiet-button" type="button" disabled={saveState === 'loading' || createState === 'loading' || restoreState === 'loading' || !!editor.active} onClick={showLibrary}>图纸库</button>}
          {(createState === 'error' || saveState === 'error') && (
            <span className="api-error">本机保存失败</span>
          )}
          {restoreState === 'loading' && <span className="restore-state">正在恢复上次图纸…</span>}
          {restoreState === 'error' && <span className="api-error">上次图纸无法打开</span>}
          <button className="quiet-button" type="button" disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'} onClick={() => { setImportKind('image'); setImportError(null); setImportOpen(true) }}>
            导入图片
          </button>
          <button className="quiet-button" type="button" disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'}
            onClick={() => { setImportKind('oxs'); setImportError(null); setImportOpen(true) }}>导入 OXS</button>
          <button
            className="quiet-button new-project-button"
            type="button"
            onClick={startBlankProject}
            disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'}
          >
            {createState === 'loading' ? '创建中…' : '新建图纸'}
          </button>
          {!libraryOpen && mode === 'edit' && <button
            className="primary-button"
            type="button"
            disabled={!dirty || !session || !!editor.active || saveState === 'loading'}
            onClick={saveRevision}
          >
            {saveState === 'loading' ? '保存中…' : '保存版本'}
          </button>}
        </div>
      </header>

      {libraryOpen ? <ProjectLibrary onOpen={openLibraryProject} onBusy={setLibraryBusy}
        onRemoved={(id) => {
          if (session?.projectId === id) {
            setSession(null)
            window.localStorage.removeItem(LAST_PROJECT_KEY)
          }
        }}
        onRenamed={(id, name, head) => {
          if (session?.projectId === id) {
            setSession({ projectId: id, headRevisionId: head })
            dispatch({ type: 'reset', document: { ...document, metadata: { ...document.metadata, title: name } } })
          }
        }} /> : <section className="workspace-grid">
        <aside className="tool-rail" aria-label="绘图工具">
          {mode === 'view' ? <div className="view-help"><strong>看图</strong><p>点击针格查线色</p><p>拖动平移<br />滚轮缩放</p></div> : TOOLS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={tool === item.id ? 'tool-button active' : 'tool-button'}
              onClick={() => setTool(item.id)}
              aria-pressed={tool === item.id}
            >
              <span>{item.label}</span>
              <kbd>{item.shortcut}</kbd>
            </button>
          ))}

        </aside>

        <section className="canvas-stage" aria-label="图纸编辑区">
          <div className="canvas-toolbar">
            <div className="view-tabs" aria-label="视图模式">
              <button
                className={viewMode === 'pattern' ? 'active' : ''}
                type="button"
                onClick={() => setViewMode('pattern')}
              >
                针格
              </button>
              <button
                className={viewMode === 'source' ? 'active' : ''}
                type="button"
                disabled={!sourceImageUrl}
                onClick={() => setViewMode('source')}
              >
                原图对照
              </button>
            </div>
            <div className="canvas-meta">
              <button className="fit-view-button" type="button" onClick={() => {
                setFitRequest((current) => current + 1)
                setSourceFullView(true)
              }}>适应窗口</button>
              <span>{document.grid.width} × {document.grid.height} 格</span>
              <span>{document.fabric.count} CT</span>
              <span>{document.recipes.length} 色</span>
            </div>
          </div>
          <div className="edit-toolbar" role="toolbar" aria-label="查看和编辑操作">
            <div className="mode-tabs">
              <button type="button" aria-pressed={mode === 'view'} onClick={() => { setMode('view'); setSelection(null) }}>查看模式</button>
              <button type="button" aria-pressed={mode === 'edit'} onClick={() => { setMode('edit'); setHighlightedRecipe(null) }}>编辑模式</button>
            </div>
            <span className="toolbar-divider" />
            {mode === 'edit' ? <>

            <button type="button" disabled={!editor.past.length || !!editor.active}
              title="撤销 · ⌘/Ctrl+Z" onClick={() => dispatch({ type: 'undo' })}>撤销</button>
            <button type="button" disabled={!editor.future.length || !!editor.active}
              title="重做 · ⌘/Ctrl+Shift+Z / Ctrl+Y" onClick={() => dispatch({ type: 'redo' })}>重做</button>
            <span className="toolbar-divider" />
            <button type="button" disabled={!selection || !!editor.active} onClick={() => selection &&
              paintCells(selectionIndices(selection, document.grid.width), selectedRecipe, '填充选区')}>填充选区</button>
            <button type="button" disabled={!selection || !!editor.active} onClick={() => selection &&
              paintCells(selectionIndices(selection, document.grid.width), 0, '清空选区')}>清空选区</button>
            <button type="button" disabled={!selection} onClick={() => setSelection(null)}>取消选区</button>
            </> : <>
              <button type="button" aria-pressed={showSymbols} onClick={() => setShowSymbols((current) => !current)}>符号显示</button>
              <button type="button" aria-pressed={showGrid} onClick={() => setShowGrid((current) => !current)}>网格</button>
              <button type="button" disabled={highlightedRecipe === null} onClick={() => setHighlightedRecipe(null)}>取消颜色高亮</button>
              {highlightedRecipe !== null && <span className="color-count">当前颜色 {recipeCounts.get(highlightedRecipe) ?? 0} 针</span>}
            </>}
          </div>
          <div className={`comparison-workspace${viewMode === 'source' && sourceImageUrl ? ' is-comparing' : ''}`}>
            {viewMode === 'source' && sourceImageUrl && (
              <section className="comparison-pane" aria-label="原图视图">
                <div className="pane-heading"><strong>原图</strong>
                  <button type="button" onClick={() => setSourceFullView((current) => !current)}>
                    {sourceFullView ? '放大查看' : '显示全图'}
                  </button>
                </div>
                <div className={`source-preview${sourceFullView ? ' source-fit' : ''}`}>
                  <img src={sourceImageUrl} alt="本次打版使用的原图" />
                </div>
              </section>
            )}
            <section className="comparison-pane pattern-pane" aria-label="图纸视图" key="pattern-pane">
              {viewMode === 'source' && sourceImageUrl && <div className="pane-heading"><strong>{mode === 'view' ? '图纸 · 点击查线色' : '图纸 · 可直接编辑'}</strong></div>}
              <PatternCanvas
                key={`${document.grid.width}x${document.grid.height}:${fitRequest}`}
                document={document}
                readOnly={mode === 'view'}
                highlightedRecipe={highlightedRecipe}
                showSymbols={showSymbols}
                showGrid={showGrid}
                tool={tool}
                selectedRecipe={selectedRecipe}
                selection={selection}
                onPaint={paintCells}
                onEditBegin={(label) => dispatch({ type: 'begin', label })}
                onEditEnd={() => dispatch({ type: 'end' })}
                onPick={pickCell}
                onSelect={setSelection}
                onHover={setHoveredCell}
              />
            </section>
          </div>
          <div className="status-bar">
            <span>{hoveredCell ? `列 ${hoveredCell.col + 1}，行 ${hoveredCell.row + 1}` : '移动指针查看坐标'}</span>
            <span>{mode === 'view' ? '点击查线色 · 拖动平移 · 滚轮缩放' : '滚轮缩放 · 按住空格拖动 · 中键平移'}{showSymbols ? ' · 放大查看符号' : ''}</span>
          </div>
        </section>

        <aside className="inspector">
          <section className="inspector-section current-cell-panel" aria-label="实时针格信息">
            <div className="section-heading"><h2>针格信息</h2><span>{mode === 'view' ? '查看' : '编辑'}</span></div>
            <div className="live-cell-position">{hoveredCell ? `列 ${hoveredCell.col + 1} · 行 ${hoveredCell.row + 1}` : '移动鼠标到图纸上'}</div>
            <p>{hoveredRecipeIndex === null ? '坐标与线色会实时显示在这里' : hoveredRecipeIndex === 0 ? '空白针格' : `${hoveredMaterial?.brand ?? 'DMC'} ${hoveredMaterial?.code ?? '未知'} · ${hoveredMaterial?.name ?? ''}`}</p>
            {selection && <span className="selection-count">已选 {(selection.endCol - selection.startCol + 1) * (selection.endRow - selection.startRow + 1)} 格</span>}
          </section>
          <section className="inspector-section">
            <div className="section-heading">
              <h2>线色</h2>
              <span>{palette.length} 个配方</span>
            </div>
            <div className="palette-list">
              {palette.map((recipe) => (
                <button
                  key={recipe.id}
                  type="button"
                  className={(mode === 'view' ? highlightedRecipe : selectedRecipe) === recipe.index ? 'palette-row active' : 'palette-row'}
                  data-recipe-index={recipe.index}
                  aria-pressed={(mode === 'view' ? highlightedRecipe : selectedRecipe) === recipe.index}
                  onClick={() => {
                    if (mode === 'view') {
                      setHighlightedRecipe((current) => current === recipe.index ? null : recipe.index)
                    } else {
                      setSelectedRecipe(recipe.index)
                      setTool('paint')
                    }
                  }}
                >
                  <span
                    className="thread-swatch"
                    style={{ background: recipe.material?.color.display_srgb ?? '#B9BEC7' }}
                  />
                  <span className="thread-name">
                    <strong>{showSymbols && <span className="recipe-symbol">{symbols.get(recipe.index)}</span>}{recipe.material?.brand ?? 'DMC'} {recipe.material?.code ?? '未知'}</strong>
                    <small>{recipe.material?.name ?? '未分配材料'}</small>
                    <small>{recipeCounts.get(recipe.index) ?? 0} 针</small>
                  </span>
                  <span className="strand-count">{recipe.components[0]?.strands ?? 2} 股</span>
                </button>
              ))}
            </div>
          </section>

          {mode === 'edit' && <section className="inspector-section history-panel">
            <div className="section-heading"><h2>操作记录</h2><span>本次会话 · 最近 100 步</span></div>
            {!editor.past.length && <p>暂无编辑操作</p>}
            <ol>{editor.past.slice(-8).reverse().map((edit, index) => (
              <li key={editor.past.length - index}>{edit.label}<span>{edit.changes.length} 格</span></li>
            ))}</ol>
            {!!editor.future.length && <p>可重做 {editor.future.length} 步</p>}
            <p>⌘/Ctrl+Z 撤销 · ⌘/Ctrl+Shift+Z 重做<br />⌘/Ctrl+S 保存 · Esc 取消选区</p>
          </section>}

          {importNotice && <section className="inspector-section"><h2>导入说明</h2><p className="import-explanation">{importNotice}</p></section>}
          {planningReport && <section className="inspector-section">
            <div className="section-heading"><h2>自动方案</h2><span>{planningReport.provider === 'codex' ? 'Codex 分析' : '本地分析'}</span></div>
            <p className="import-explanation">{planningReport.plan.explanation}</p>
            {planningReport.fallback_reason && <p className="import-explanation">{planningReport.fallback_reason}</p>}
          </section>}
          <section className="inspector-section quality-panel">
            <div className="section-heading"><h2>还原设置</h2></div>
            <dl>
              <div><dt>成品尺寸</dt><dd>{(document.grid.width / document.fabric.count * 2.54).toFixed(1)} × {(document.grid.height / document.fabric.count * 2.54).toFixed(1)} cm</dd></div>
              <div><dt>绣布规格</dt><dd>{document.fabric.count} CT</dd></div>
              <div><dt>色差方法</dt><dd>CIEDE2000</dd></div>
              <div><dt>邻域规则</dt><dd>4 邻域</dd></div>
              <div><dt>色库来源</dt><dd>{embeddedOxsPalette ? 'OXS 内置线色' : 'DMC 2021 近似色'}</dd></div>
              {conversionSummary && (
                <>
                  <div>
                    <dt>针数</dt>
                    <dd>{conversionSummary.statistics.full_stitch_count.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>平均色差</dt>
                    <dd>
                      {conversionSummary.palette_report.quality.weighted_mean_delta_e00.toFixed(2)}
                    </dd>
                  </div>
                  <div>
                    <dt>底布留空</dt>
                    <dd>{conversionSummary.background_report?.removed_cells.toLocaleString() ?? '关闭'}</dd>
                  </div>
                  <div>
                    <dt>主体有效格数</dt>
                    <dd className={conversionSummary.motif_resolution.passes_minimum ? '' : 'quality-warning'}>
                      {conversionSummary.motif_resolution.effective_width} × {conversionSummary.motif_resolution.effective_height}
                    </dd>
                  </div>
                </>
              )}
            </dl>
          </section>
        </aside>
      </section>}
      {importOpen && (
        <div className="import-backdrop" role="presentation">
          <section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
            <div className="import-heading">
              <div>
                <h2 id="import-title">{importKind === 'oxs' ? '导入 OXS 图纸' : '从图片自动生成图纸'}</h2>
                <p>{importKind === 'oxs' ? '读取图纸中的针格、线色、CT 和股数，导入后直接查看。当前支持全针图纸。' : '选择图片即可。系统自动分析针格、尺寸、线色和股数，并生成图纸。'}</p>
              </div>
              <button
                type="button"
                aria-label="关闭"
                disabled={importState === 'loading'}
                onClick={() => setImportOpen(false)}
              >
                ×
              </button>
            </div>
            <form onSubmit={importImage}>
              <label className="file-field">
                <span>{importKind === 'oxs' ? 'OXS 文件' : '效果原图'}</span>
                <input name="image" type="file" accept={importKind === 'oxs' ? '.oxs' : 'image/png,image/jpeg'} required disabled={importState === 'loading'} />
                <small>{importKind === 'oxs' ? 'OXS，最大 16 MiB' : 'JPG 或 PNG，最大 20 MiB'}</small>
              </label>
              {importKind === 'image' && <p className="import-explanation">优先使用本机 Codex 分析；不可用时自动采用本地方案。生成后可在右侧查看参数与选择依据。</p>}
              {importState === 'loading' && <p role="status">{importKind === 'oxs' ? '正在读取图纸…' : '正在分析图片并生成图纸，请稍候…'}</p>}
              {importError && <p className="import-error">{importError}</p>}
              <div className="import-actions">
                <button type="button" disabled={importState === 'loading'} onClick={() => setImportOpen(false)}>
                  取消
                </button>
                <button className="primary-button" type="submit" disabled={importState === 'loading'}>
                  {importState === 'loading' ? '正在处理…' : importKind === 'oxs' ? '导入图纸' : '自动生成图纸'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </main>
  )
}

export default App
