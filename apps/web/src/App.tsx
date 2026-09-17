import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
import { useProjectDraft } from './hooks/useProjectDraft'
import { useGeneration } from './hooks/useGeneration'
import { AccountPanel } from './components/AccountPanel'
import { DownloadPanel } from './components/DownloadPanel'
import { BlankProjectDialog } from './components/BlankProjectDialog'
import type { User } from './lib/account'
import { ProjectLibrary } from './components/ProjectLibrary'
import { PatternCanvas } from './components/PatternCanvas'
import {
  ApiError,
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
import {
  IconBrush,
  IconBucket,
  IconCredit,
  IconDownload,
  IconEraser,
  IconFileCode,
  IconFolder,
  IconHand,
  IconMarquee,
  IconPipette,
  IconPlus,
  IconSparkles,
} from './components/Icons'
import type {
  CellSelection,
  ImageConversionResponse,
  PatternDocument,
  RareColorSummary,
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

const TOOL_ICONS: Record<Tool, typeof IconBrush> = {
  paint: IconBrush,
  pick: IconPipette,
  select: IconMarquee,
  pan: IconHand,
  erase: IconEraser,
  fill: IconBucket,
}


function sourceUrlFromDocument(document: PatternDocument): string | null {
  const provenance = document.provenance as
    | { sources?: { id?: string; locator?: string }[] }
    | undefined
  const locator = provenance?.sources?.find((source) => source.id === 'src_image')?.locator
  return locator?.startsWith('/api/v1/assets/') ? locator : null
}

function App({ user, onLogout, onAccountRefresh }: {
  user: User; onLogout: () => Promise<void>; onAccountRefresh: () => Promise<void>
}) {
  const generation = useGeneration(user.user_id)
  const autoOpenGeneration = useRef<{ epoch: number; document: PatternDocument } | null>(null)
  const LAST_PROJECT_KEY = `stitchforge.lastProjectId.${user.user_id}`
  const [accountOpen, setAccountOpen] = useState(() => window.location.hash.startsWith('#billing'))
  const [downloadOpen, setDownloadOpen] = useState(false)
  const [blankOpen, setBlankOpen] = useState(false)
  const [editor, dispatch] = useReducer(editorReducer, undefined, () => createEditor(makeDemoPattern()))
  const { document } = editor
  const dirty = isDirty(editor)
  const [libraryOpen, setLibraryOpen] = useState(() => window.location.hash === '#library' || !window.localStorage.getItem(LAST_PROJECT_KEY))
  const [libraryBusy, setLibraryBusy] = useState(false)
  const [mobileCreateOpen, setMobileCreateOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)
  const [mobileLayout, setMobileLayout] = useState(() => window.matchMedia('(max-width: 620px)').matches)
  const mobileCreateButtonRef = useRef<HTMLButtonElement>(null)
  const mobileCreateSheetRef = useRef<HTMLElement>(null)
  const mobileInspectorToggleRef = useRef<HTMLButtonElement>(null)
  const mobileInspectorRef = useRef<HTMLElement>(null)
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  const [highlightedRecipe, setHighlightedRecipe] = useState<number | null>(null)
  const [showSymbols, setShowSymbols] = useState(true)
  const [showGrid, setShowGrid] = useState(true)
  const [tool, setTool] = useState<Tool>('paint')
  const [selectedRecipe, setSelectedRecipe] = useState(1)
  const [hoveredCell, setHoveredCell] = useState<{ col: number; row: number } | null>(null)
  const [pickedCell, setPickedCell] = useState<{ col: number; row: number; projectId: string | null } | null>(null)
  const [selection, setSelection] = useState<CellSelection | null>(null)
  const [createState, setCreateState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [saveState, setSaveState] = useState<'idle' | 'loading' | 'error'>('idle')
  const [session, setSession] = useState<{ projectId: string; headRevisionId: string } | null>(null)
  const projectDraft = useProjectDraft(user.user_id, session, editor)
  const restoreProjectDraft = projectDraft.restore
  const [draftConflict, setDraftConflict] = useState(false)
  const [saveError, setSaveError] = useState('')
  const operationEpoch = useRef(0)
  const [viewMode, setViewMode] = useState<'pattern' | 'source'>('pattern')
  const [sourceFullView, setSourceFullView] = useState(true)
  const [fitRequest, setFitRequest] = useState(0)
  const [sourceImageUrl, setSourceImageUrl] = useState<string | null>(null)
  const [importKind, setImportKind] = useState<'image' | 'oxs'>('image')
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importState, setImportState] = useState<'idle' | 'loading' | 'error'>('idle')
  const importDialogRef = useRef<HTMLElement>(null)
  const importBusyRef = useRef(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [conversionSummary, setConversionSummary] = useState<ImageConversionResponse | null>(null)
  const [restoreState, setRestoreState] = useState<'idle' | 'loading' | 'error'>(() =>
    window.localStorage.getItem(LAST_PROJECT_KEY) ? 'loading' : 'idle',
  )

  useEffect(() => {
    window.history.replaceState(null, '', libraryOpen ? '#library' : '#pattern')
  }, [libraryOpen])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 620px)')
    function updateMobileLayout(event: MediaQueryListEvent) {
      setMobileLayout(event.matches)
      if (!event.matches) {
        closeMobileCreate(false)
        setMobileInspectorOpen(false)
      }
    }
    media.addEventListener('change', updateMobileLayout)
    return () => media.removeEventListener('change', updateMobileLayout)
  }, [])

  function closeMobileInspector(restoreFocus = true) {
    setMobileInspectorOpen(false)
    if (restoreFocus) window.requestAnimationFrame(() => mobileInspectorToggleRef.current?.focus())
  }

  function closeMobileCreate(restoreFocus = true) {
    setMobileCreateOpen(false)
    if (restoreFocus) window.requestAnimationFrame(() => mobileCreateButtonRef.current?.focus())
  }

  useEffect(() => {
    if (!mobileLayout || !mobileInspectorOpen) return
    const panel = mobileInspectorRef.current
    panel?.querySelector<HTMLElement>('button')?.focus()
    function keepFocusInside(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !panel) return
      const focusable = [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled)')]
      const first = focusable[0]
      const last = focusable.at(-1)
      if (!first || !last) return
      if (!panel.contains(window.document.activeElement) || (event.shiftKey && window.document.activeElement === first)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus()
      } else if (!event.shiftKey && window.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.document.addEventListener('keydown', keepFocusInside)
    return () => window.document.removeEventListener('keydown', keepFocusInside)
  }, [mobileLayout, mobileInspectorOpen])

  useEffect(() => {
    if (!mobileCreateOpen) return
    const sheet = mobileCreateSheetRef.current
    sheet?.querySelector<HTMLElement>('button')?.focus()
    function keepFocusInside(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeMobileCreate()
        return
      }
      if (event.key !== 'Tab' || !sheet) return
      const focusable = [...sheet.querySelectorAll<HTMLElement>('button:not(:disabled)')]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && window.document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && window.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.document.addEventListener('keydown', keepFocusInside)
    return () => {
      window.document.removeEventListener('keydown', keepFocusInside)
    }
  }, [mobileCreateOpen])

  useEffect(() => { importBusyRef.current = importState === 'loading' }, [importState])
  useEffect(() => {
    if (!importOpen) return
    const previousFocus = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
    const dialog = importDialogRef.current
    dialog?.querySelector<HTMLElement>('input, button')?.focus()
    function keepFocusInside(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        if (!importBusyRef.current) setImportOpen(false)
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href]')]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && window.document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && window.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.document.addEventListener('keydown', keepFocusInside)
    return () => { window.document.removeEventListener('keydown', keepFocusInside); previousFocus?.focus() }
  }, [importOpen])

  const palette = useMemo(() => {
    const materials = new Map(document.materials.map((item) => [item.id, item]))
    return document.recipes.map((recipe) => ({
      ...recipe,
      material: materials.get(recipe.components[0]?.material_id ?? ''),
    }))
  }, [document.materials, document.recipes])

  const symbols = useMemo(() => recipeSymbols({ recipes: document.recipes, legend: document.legend }), [document.recipes, document.legend])
  const recipeCounts = editor.recipeCounts
  const rareColorSummary = document.extensions?.['stitchforge.rare_colors'] as RareColorSummary | undefined

  const activeCell = hoveredCell ?? (pickedCell?.projectId === (session?.projectId ?? null) ? pickedCell : null)
  const hoveredRecipeIndex = activeCell ? document.full_stitches.cells[activeCell.row * document.grid.width + activeCell.col] : null
  const hoveredMaterial = palette.find((recipe) => recipe.index === hoveredRecipeIndex)?.material
  const planningReport = conversionSummary?.planning_report ??
    (document.extensions?.['stitchforge.import_plan'] as ImageConversionResponse['planning_report'] | undefined)
  const embeddedOxsPalette = (document.provenance as { catalog_snapshot_id?: string } | undefined)?.catalog_snapshot_id === 'oxs_embedded_palette'
  const basicPalette = document.materials.length > 0 && document.materials.every(material => material.brand === '自定义' && material.id.startsWith('m_basic_'))

  useEffect(() => {
    if (highlightedRecipe === null || mode !== 'view') return
    window.document.querySelector(`[data-recipe-index="${highlightedRecipe}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [highlightedRecipe, mode])

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const target = event.target
      if (event.key === 'Escape' && mobileInspectorOpen) {
        closeMobileInspector()
        return
      }
      if (downloadOpen || accountOpen || blankOpen || mobileInspectorOpen || libraryOpen || importOpen || target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
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
    const epoch = ++operationEpoch.current
    async function restore() {
      try {
        const project = await getProject(projectId as string)
        if (!project.head_revision_id || project.deleted_at_ms) throw new Error('图纸已移入回收站或没有可打开版本')
        const revision = await getRevision(project.project_id, project.head_revision_id)
        const restored = await restoreProjectDraft(project.project_id, revision.revision_id, revision.document)
        if (cancelled || epoch !== operationEpoch.current) return
        dispatch({ type: 'restore', state: restored.editor })
        setDraftConflict(restored.conflict)
        setHighlightedRecipe(null)
        setSession({
          projectId: project.project_id,
          headRevisionId: restored.baseRevisionId,
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
  }, [LAST_PROJECT_KEY, restoreProjectDraft])

  const paintCells = useCallback((indices: readonly number[], recipeIndex: number, label = '画笔') => {
    dispatch({ type: 'paint', indices, recipe: recipeIndex, label })
  }, [])

  function pickCell(index: number) {
    setPickedCell({ col: index % document.grid.width, row: Math.floor(index / document.grid.width), projectId: session?.projectId ?? null })
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
    if (dirty && !window.confirm('当前图纸有未保存修改，返回图纸库？重新打开时将尝试恢复本地草稿。')) return
    setMobileInspectorOpen(false)
    setLibraryOpen(true)
  }

  async function openLibraryProject(projectId: string, discardDraft = false) {
    const epoch = ++operationEpoch.current
    const project = await getProject(projectId)
    if (!project.head_revision_id || project.deleted_at_ms) throw new Error('图纸已移入回收站或没有可打开版本')
    const revision = await getRevision(projectId, project.head_revision_id)
    const restored = discardDraft
      ? { editor: createEditor(revision.document), baseRevisionId: revision.revision_id, conflict: false }
      : await restoreProjectDraft(projectId, revision.revision_id, revision.document)
    if (epoch !== operationEpoch.current) return
    // Delete the draft only after the server version has actually been fetched.
    if (discardDraft) await projectDraft.discard(projectId)
    if (epoch !== operationEpoch.current) return
    dispatch({ type: 'restore', state: restored.editor })
    setDraftConflict(restored.conflict)
    setSession({ projectId, headRevisionId: restored.baseRevisionId })
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
    setMobileInspectorOpen(false)
    setLibraryOpen(false)
  }

  function requestBlankProject() {
    if (dirty && !window.confirm('当前图纸有未保存修改，继续新建图纸？重新打开时将尝试恢复本地草稿。')) return
    setBlankOpen(true)
  }

  async function startBlankProject(name: string, width: number, height: number) {
    const epoch = ++operationEpoch.current
    setCreateState('loading')
    try {
      const created = await createProject(name, { width, height })
      if (epoch !== operationEpoch.current) return
      setDraftConflict(false)
      dispatch({ type: 'reset', document: created.initial_revision.document })
      setHighlightedRecipe(null)
      setSelectedRecipe(created.initial_revision.document.recipes[0]?.index ?? 0)
      setTool('paint')
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
      setMode('edit')
      setHoveredCell(null)
      setFitRequest((value) => value + 1)
      setLibraryOpen(false)
      setCreateState('idle')
      setBlankOpen(false)
    } catch (error) {
      setCreateState('error')
      throw error
    }
  }

  async function importImage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const file = new FormData(event.currentTarget).get('image')
    if (!(file instanceof File) || file.size === 0) { setImportError('请选择文件'); return }
    if (dirty && !window.confirm(projectDraft.status.includes('失败')
      ? '本地草稿保存失败，继续导入会丢失未保存修改。建议取消并先保存版本，仍要继续？'
      : '当前图纸有未保存修改，继续导入新图纸？重新打开时将尝试恢复本地草稿。')) return
    const epoch = ++operationEpoch.current
    setImportState('loading')
    setImportError(null)
    try {
      if (importKind === 'oxs') {
        if (file.size > 16 * 1024 * 1024) throw new Error('OXS 文件超过 16 MiB 限制')
        const imported = await importOxsProject(file)
        if (epoch !== operationEpoch.current) return
        setDraftConflict(false)
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
      if (user.credits < 1 && !generation.pending) throw new Error('生成次数不足，请先充值。')
      if (file.size > 20 * 1024 * 1024) throw new Error('图片文件超过 20 MiB 限制')
      autoOpenGeneration.current = { epoch, document }
      await generation.start(file)
      if (epoch !== operationEpoch.current) return
      setImportOpen(false)
      setImportState('idle')
    } catch (error) {
      if (epoch !== operationEpoch.current) return
      setImportState('error')
      setImportError(error instanceof Error ? error.message : '导入失败')
    }
  }

  useEffect(() => {
    const result = generation.job?.result
    const target = autoOpenGeneration.current
    if (generation.job?.state !== 'SUCCEEDED' || !result || !target) return
    autoOpenGeneration.current = null
    if (target.epoch !== operationEpoch.current || target.document !== document) return
    dispatch({ type: 'reset', document: result.revision.document })
    setSession({ projectId: generation.job.project_id, headRevisionId: result.revision.revision_id })
    window.localStorage.setItem(LAST_PROJECT_KEY, generation.job.project_id)
    setSourceImageUrl(result.normalized_asset.url)
    setConversionSummary(result)
    setSelectedRecipe(result.revision.document.recipes[0]?.index ?? 0)
    setHighlightedRecipe(null)
    setSelection(null)
    setMode('view')
    setViewMode('pattern')
    setFitRequest(value => value + 1)
    setLibraryOpen(false)
    setDraftConflict(false)
    generation.acknowledge()
    void onAccountRefresh()
  }, [generation, document, LAST_PROJECT_KEY, onAccountRefresh])

  async function openGeneratedProject() {
    if (!generation.job || generation.job.state !== 'SUCCEEDED') return
    if (dirty && !window.confirm(projectDraft.status.includes('失败')
      ? '本地草稿保存失败，打开其他图纸会丢失未保存修改。建议取消并先保存版本，仍要继续？'
      : '当前图纸有未保存修改，打开生成图纸？重新打开时将尝试恢复本地草稿。')) return
    await openLibraryProject(generation.job.project_id)
    generation.acknowledge()
    void onAccountRefresh()
  }

  async function prepareDownload() {
    if (!session || editor.active || saveState === 'loading') throw new Error('图纸尚未就绪，请稍后再试')
    if (!dirty) return session
    const epoch = operationEpoch.current
    setSaveError('')
    setSaveState('loading')
    try {
      const revision = await createRevision(session.projectId, session.headRevisionId, document)
      if (epoch !== operationEpoch.current) throw new Error('图纸已切换，请重新下载')
      const savedSession = { ...session, headRevisionId: revision.revision_id }
      setSession(savedSession)
      dispatch({ type: 'saved', document })
      setDraftConflict(false)
      setSaveState('idle')
      return savedSession
    } catch (error) {
      if (epoch === operationEpoch.current) {
        setSaveState('error')
        setSaveError(error instanceof Error ? error.message : '保存失败')
        if (error instanceof ApiError && error.code === 'REVISION_CONFLICT') setDraftConflict(true)
      }
      throw error
    }
  }

  async function saveRevision() {
    if (!session || !dirty || editor.active || saveState === 'loading') return
    const epoch = operationEpoch.current
    setSaveError('')
    setSaveState('loading')
    try {
      const revision = await createRevision(session.projectId, session.headRevisionId, document)
      if (epoch !== operationEpoch.current) return
      setSession({ ...session, headRevisionId: revision.revision_id })
      window.localStorage.setItem(LAST_PROJECT_KEY, session.projectId)
      dispatch({ type: 'saved', document })
      setDraftConflict(false)
      setSaveState('idle')
    } catch (error) {
      if (epoch !== operationEpoch.current) return
      setSaveState('error')
      setSaveError(error instanceof Error ? error.message : '保存失败')
      if (error instanceof ApiError && error.code === 'REVISION_CONFLICT') setDraftConflict(true)
    }
  }

  async function loadServerVersion() {
    if (!session || !window.confirm('放弃当前本地草稿并载入服务器版本？')) return
    await openLibraryProject(session.projectId, true)
    setSaveError('')
  }

  async function saveDraftCopy() {
    if (!session || saveState === 'loading') return
    const epoch = ++operationEpoch.current
    setSaveState('loading')
    try {
      const created = await createProject(`${document.metadata.title} · 草稿副本`)
      const copy = { ...document, metadata: { ...document.metadata, title: `${document.metadata.title} · 草稿副本` } }
      const saved = await createRevision(created.project.project_id, created.initial_revision.revision_id, copy)
      if (epoch !== operationEpoch.current) return
      dispatch({ type: 'reset', document: copy })
      setSession({ projectId: created.project.project_id, headRevisionId: saved.revision_id })
      window.localStorage.setItem(LAST_PROJECT_KEY, created.project.project_id)
      setDraftConflict(false)
      setSaveState('idle')
      setSaveError('')
    } catch (error) {
      if (epoch !== operationEpoch.current) return
      setSaveState('error')
      setSaveError(error instanceof Error ? error.message : '另存失败，本地草稿已保留')
    }
  }

  return (
    <><a className="skip-link" href="#main-content" inert={mobileLayout && mobileInspectorOpen}>跳到主要内容</a><main id="main-content" className={`workbench-shell${libraryOpen ? ' library-open' : ''}`}>
      <header className={`app-header${libraryOpen ? ' library-app-header' : ''}`} inert={mobileLayout && mobileInspectorOpen}>
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">SF</span>
          <div>
            <strong>StitchForge</strong>
            <span>十字绣打版工作台</span>
          </div>
        </div>
        <div className="document-title">
          <strong>{libraryOpen ? '图纸库' : document.metadata.title}</strong>
          <span>{libraryOpen ? '管理你的所有作品' : dirty ? '有未保存修改' : session ? '已保存到账号' : '示例预览'}</span>
        </div>
        <div className="mobile-document-title">
          <h1>{libraryOpen ? '我的图纸' : document.metadata.title}</h1>
          <span>{libraryOpen ? '作品库' : `${document.grid.width} × ${document.grid.height} 格 · ${dirty ? '未保存' : session ? '已保存' : '预览'}`}</span>
        </div>
        <div className="header-actions">
          <button className="quiet-button account-button" type="button" disabled={importState === 'loading'} onClick={() => setAccountOpen(true)}>
            <IconCredit aria-hidden="true" />
            <span>{user.credits} 次<span className="account-label-suffix"> / 账号</span></span>
          </button>
          {!libraryOpen && <button className="quiet-button" type="button" disabled={saveState === 'loading' || createState === 'loading' || restoreState === 'loading' || !!editor.active} onClick={showLibrary}>
            <IconFolder aria-hidden="true" />
            <span>图纸库</span>
          </button>}
          {(createState === 'error' || saveState === 'error') && (
            <span className="api-error">保存失败</span>
          )}
          {restoreState === 'loading' && <span className="restore-state">正在恢复上次图纸…</span>}
          {restoreState === 'error' && <span className="api-error">上次图纸无法打开</span>}
          <button className="quiet-button library-create-action" type="button" disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'} onClick={() => { setImportKind('image'); setImportError(null); setImportOpen(true) }}>
            <IconSparkles aria-hidden="true" />
            <span>导入图片</span>
          </button>
          <button className="quiet-button library-create-action" type="button" disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'}
            onClick={() => { setImportKind('oxs'); setImportError(null); setImportOpen(true) }}>
            <IconFileCode aria-hidden="true" />
            <span>导入 OXS</span>
          </button>
          <button
            className="quiet-button new-project-button library-create-action"
            type="button"
            onClick={requestBlankProject}
            disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'}
          >
            <IconPlus aria-hidden="true" />
            <span>{createState === 'loading' ? '创建中…' : '新建图纸'}</span>
          </button>
          {libraryOpen && <button
            ref={mobileCreateButtonRef}
            className="primary-button mobile-create-button"
            type="button"
            aria-haspopup="dialog"
            aria-expanded={mobileCreateOpen}
            disabled={libraryBusy || createState === 'loading' || saveState === 'loading' || restoreState === 'loading'}
            onClick={() => setMobileCreateOpen(true)}
          >
            <IconPlus aria-hidden="true" />
            <span>创建图纸</span>
          </button>}
          {!libraryOpen && mode === 'edit' && <button
            className="primary-button"
            type="button"
            disabled={!dirty || !session || !!editor.active || saveState === 'loading'}
            onClick={saveRevision}
          >
            {saveState === 'loading' ? '保存中…' : '保存版本'}
          </button>}
          {!libraryOpen && <button className="primary-button" type="button"
            disabled={!session || !!editor.active || saveState === 'loading' || createState === 'loading' || restoreState === 'loading' || importState === 'loading'}
            onClick={() => setDownloadOpen(true)}>
            <IconDownload aria-hidden="true" />
            <span>下载图纸</span>
          </button>}
        </div>
      </header>

      <div className="workbench-notices" inert={mobileLayout && mobileInspectorOpen}>
      {projectDraft.status.includes('失败') && <p className="draft-status api-error" role="alert">{projectDraft.status}</p>}
      {(generation.pending || generation.status || generation.error) && <div className="generation-status" role="status">
        <span>{generation.status}</span>
        {generation.error && <span className="api-error">{generation.error}</span>}
        {generation.job?.state === 'SUCCEEDED' && <button type="button" onClick={() => { void openGeneratedProject().catch(error => setSaveError(String(error))) }}>打开生成图纸</button>}
        {generation.pending && generation.canAbandon && <button type="button" disabled={generation.uploading || generation.abandoning} onClick={() => {
          if (window.confirm('放弃本次未完成的上传？空白占位图纸将移入回收站，已完成的生成结果会保留。')) void generation.abandonUpload()
        }}>{generation.abandoning ? '正在确认…' : '放弃本次上传'}</button>}
        {generation.pending && !generation.canAbandon && generation.job?.state !== 'SUCCEEDED' && <button type="button" disabled={generation.uploading || generation.abandoning || generation.job?.state === 'CANCEL_REQUESTED'} onClick={() => { void generation.cancel() }}>取消生成</button>}
        {generation.pending && generation.error && <button type="button" disabled={generation.uploading || generation.abandoning} onClick={generation.retry}>继续查询</button>}
        {!generation.pending && <button type="button" onClick={generation.acknowledge}>关闭提示</button>}
      </div>}
      {saveError && <p className="draft-status api-error" role="alert">{saveError}</p>}
      {draftConflict && !libraryOpen && <div className="draft-conflict" role="alert">
        <span>服务器已有新版本，本地草稿已保留。</span>
        <button type="button" disabled={saveState === 'loading'} onClick={saveDraftCopy}>另存为新图纸</button>
        <button type="button" disabled={saveState === 'loading'} onClick={() => { void loadServerVersion().catch(error => setSaveError(String(error))) }}>载入服务器版本</button>
      </div>}
      </div>
      {libraryOpen ? <ProjectLibrary onOpen={openLibraryProject} onBusy={setLibraryBusy}
        onRemoved={(id) => {
          if (session?.projectId === id) {
            setSession(null)
            window.localStorage.removeItem(LAST_PROJECT_KEY)
          }
        }}
        onRenamed={(id, name, head, previousHead) => {
          if (session?.projectId === id) {
            if (session.headRevisionId === previousHead) {
              setSession({ projectId: id, headRevisionId: head })
              dispatch({ type: 'rename', title: name })
            } else {
              // A rename based on a newer server version must not rebase stale cells.
              setDraftConflict(true)
            }
          }
        }} /> : <section className="workspace-grid">
        <aside className="tool-rail" aria-label="绘图工具" inert={mobileLayout && mobileInspectorOpen}>
          {mode === 'view' ? <div className="view-help"><strong>看图</strong><p>点击针格查线色</p><p>拖动平移<br />双指缩放</p></div> : TOOLS.map((item) => {
            const ToolIcon = TOOL_ICONS[item.id]
            return (
              <button
                key={item.id}
                type="button"
                className={tool === item.id ? 'tool-button active' : 'tool-button'}
                onClick={() => setTool(item.id)}
                aria-pressed={tool === item.id}
              >
                <ToolIcon className="tool-btn-icon" aria-hidden="true" />
                <span>{item.label}</span>
                <kbd>{item.shortcut}</kbd>
              </button>
            )
          })}
        </aside>

        <section className="canvas-stage" aria-label="图纸编辑区" inert={mobileLayout && mobileInspectorOpen}>
          <div className="canvas-toolbar">
            <div className="view-tabs" aria-label="视图模式">
              <button
                className={viewMode === 'pattern' ? 'active' : ''}
                type="button"
                aria-pressed={viewMode === 'pattern'}
                onClick={() => setViewMode('pattern')}
              >
                针格
              </button>
              <button
                className={viewMode === 'source' ? 'active' : ''}
                type="button"
                aria-pressed={viewMode === 'source'}
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
            <button
              ref={mobileInspectorToggleRef}
              className="mobile-inspector-toggle"
              type="button"
              aria-controls="pattern-inspector"
              aria-expanded={mobileInspectorOpen}
              onClick={() => setMobileInspectorOpen((current) => !current)}
            >
              <span aria-hidden="true" className="mobile-thread-dot" style={{ background: palette.find((recipe) => recipe.index === (mode === 'view' ? highlightedRecipe : selectedRecipe))?.material?.color.display_srgb ?? '#B9BEC7' }} />
              线色与信息
            </button>
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
            <span>{activeCell ? `列 ${activeCell.col + 1}，行 ${activeCell.row + 1}` : '点击或移动到针格查看坐标'}</span>
            {projectDraft.status ? <span role="status">{projectDraft.status}</span> : <span className="interaction-hint">{mode === 'view' ? '点击查线色 · 拖动平移 · 滚轮缩放' : '滚轮缩放 · 按住空格拖动 · 中键平移'}{showSymbols ? ' · 放大查看符号' : ''}</span>}
          </div>
        </section>

        {mobileInspectorOpen && <button className="mobile-inspector-backdrop" type="button" tabIndex={-1} aria-label="关闭线色与信息" onClick={() => closeMobileInspector()} />}
        <aside ref={mobileInspectorRef} id="pattern-inspector" className={`inspector${mobileInspectorOpen ? ' is-mobile-open' : ''}`} aria-label="线色与图纸信息"
          role={mobileLayout ? 'dialog' : undefined} aria-modal={mobileLayout && mobileInspectorOpen ? true : undefined}
          aria-hidden={mobileLayout && !mobileInspectorOpen ? true : undefined} inert={mobileLayout && !mobileInspectorOpen}>
          <div className="mobile-inspector-heading">
            <span aria-hidden="true" />
            <div><strong>线色与图纸信息</strong><small>{mode === 'view' ? '选择线色以高亮针格' : '选择线色后继续绘制'}</small></div>
            <button type="button" aria-label="关闭线色与信息" onClick={() => closeMobileInspector()}>×</button>
          </div>
          <section className="inspector-section current-cell-panel" aria-label="实时针格信息">
            <div className="section-heading"><h2>针格信息</h2><span>{mode === 'view' ? '查看' : '编辑'}</span></div>
            <div className="live-cell-position">{activeCell ? `列 ${activeCell.col + 1} · 行 ${activeCell.row + 1}` : '点击针格查看线色'}</div>
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
                    if (mobileLayout) closeMobileInspector()
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
            <div className="section-heading"><h2>操作记录</h2><span>最近 100 步 · 未保存草稿可恢复</span></div>
            {!editor.past.length && <p>暂无编辑操作</p>}
            <ol>{editor.past.slice(-8).reverse().map((edit, index) => (
              <li key={editor.past.length - index}>{edit.label}<span>{edit.changes.length} 格</span></li>
            ))}</ol>
            {!!editor.future.length && <p>可重做 {editor.future.length} 步</p>}
            <p>⌘/Ctrl+Z 撤销 · ⌘/Ctrl+Shift+Z 重做<br />⌘/Ctrl+S 保存 · Esc 取消选区</p>
          </section>}

          {importNotice && <section className="inspector-section"><h2>导入说明</h2><p className="import-explanation">{importNotice}</p></section>}
          {rareColorSummary?.version === 1 && <section className="inspector-section">
            <div className="section-heading"><h2>线色精简</h2><span>生成时 {rareColorSummary.colors_before} → {rareColorSummary.colors_after} 色</span></div>
            <p className="import-explanation">{rareColorSummary.changed_stitches > 0
              ? `生成时合并了 ${rareColorSummary.colors_before - rareColorSummary.colors_after} 种近似线色，调整 ${rareColorSummary.changed_stitches} 针。`
              : '未找到适合自动合并的少量线色，保留原配色。'}</p>
            {rareColorSummary.retained_colors > 0 && <p className="import-explanation">另有 {rareColorSummary.retained_colors} 种少量线色未满足合并条件，已保留。</p>}
          </section>}
          {planningReport && <section className="inspector-section">
            <div className="section-heading"><h2>自动方案</h2><span>{planningReport.method === 'counted_stitches' ? '按原图针脚还原' : planningReport.provider === 'deepseek' ? 'DeepSeek 分析' : planningReport.provider === 'codex' ? 'Codex 分析' : '本地分析'}</span></div>
            <p className="import-explanation">{planningReport.plan.explanation}</p>
            {planningReport.structure_review && <p className="import-explanation">
              <strong>{planningReport.structure_review.status === 'passed' ? '原图结构复核通过' : '本地结构检查通过'}</strong>
              <br />{planningReport.structure_review.summary}
            </p>}
            {planningReport.fallback_reason && <p className="import-explanation">{planningReport.fallback_reason}</p>}
          </section>}
          <section className="inspector-section quality-panel">
            <div className="section-heading"><h2>还原设置</h2></div>
            <dl>
              <div><dt>成品尺寸</dt><dd>{(document.grid.width / document.fabric.count * 2.54).toFixed(1)} × {(document.grid.height / document.fabric.count * 2.54).toFixed(1)} cm</dd></div>
              <div><dt>绣布规格</dt><dd>{document.fabric.count} CT</dd></div>
              <div><dt>色差方法</dt><dd>CIEDE2000</dd></div>
              <div><dt>邻域规则</dt><dd>4 邻域</dd></div>
              <div><dt>色库来源</dt><dd>{embeddedOxsPalette ? 'OXS 内置线色' : basicPalette ? '基础自定义颜色（非实体色卡）' : 'DMC 2021 近似色'}</dd></div>
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
      {mobileCreateOpen && (
        <div className="mobile-create-backdrop" role="presentation" onPointerDown={(event) => {
          if (event.target === event.currentTarget) closeMobileCreate()
        }}>
          <section ref={mobileCreateSheetRef} className="mobile-create-sheet" role="dialog" aria-modal="true" aria-labelledby="mobile-create-title">
            <div className="mobile-create-heading">
              <div><h2 id="mobile-create-title">创建图纸</h2><p>选择最适合你的开始方式</p></div>
              <button type="button" aria-label="关闭创建菜单" onClick={() => closeMobileCreate()}>×</button>
            </div>
            <div className="mobile-create-options">
              <button type="button" onClick={() => { closeMobileCreate(false); setImportKind('image'); setImportError(null); setImportOpen(true) }}>
                <IconSparkles aria-hidden="true" /><span><strong>图片生成</strong><small>上传照片或插画 · 消耗 1 次</small></span>
              </button>
              <button type="button" onClick={() => { closeMobileCreate(false); setImportKind('oxs'); setImportError(null); setImportOpen(true) }}>
                <IconFileCode aria-hidden="true" /><span><strong>导入 OXS</strong><small>保留已有针格和线色 · 免费</small></span>
              </button>
              <button type="button" onClick={() => { closeMobileCreate(false); requestBlankProject() }}>
                <IconPlus aria-hidden="true" /><span><strong>空白图纸</strong><small>从一张空白针格开始</small></span>
              </button>
            </div>
          </section>
        </div>
      )}
      {importOpen && (
        <div className="import-backdrop" role="presentation">
          <section ref={importDialogRef} className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
            <div className="import-heading">
              <div>
                <h2 id="import-title">{importKind === 'oxs' ? '导入 OXS 图纸' : '从图片自动生成图纸'}</h2>
                <p>{importKind === 'oxs' ? '读取图纸中的针格、线色、CT 和股数，导入后直接查看。当前支持全针图纸。' : '选择图片即可。独立图案默认背景留白，自动收紧空白边距；简单图案使用更少的针格和线色。'}</p>
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
              <p className="billing-notice">{importKind === 'oxs' ? '免费导入 · 查看与编辑不扣次数' : `本次消耗 1 次 · 当前剩余 ${user.credits} 次`}</p>
              {importKind === 'image' && user.credits < 1 && <button type="button" className="quiet-button" onClick={() => { setImportOpen(false); setAccountOpen(true) }}>查看充值与次数</button>}
              <label className="file-field">
                <span>{importKind === 'oxs' ? 'OXS 文件' : '效果原图'}</span>
                <input name="image" type="file" accept={importKind === 'oxs' ? '.oxs' : 'image/png,image/jpeg'} required disabled={importState === 'loading'} />
                <small>{importKind === 'oxs' ? 'OXS，最大 16 MiB' : 'JPG 或 PNG，最大 20 MiB'}</small>
              </label>
              {importKind === 'image' && <p className="import-explanation">生成成功并保存后扣 1 次，失败不扣次数。根据服务配置，原图可能发送给受托模型服务商进行结构分析；详见 <a href="/privacy.html" target="_blank" rel="noopener">隐私政策</a>。</p>}
              {importState === 'loading' && <p role="status">{importKind === 'oxs' ? '正在读取图纸…' : '正在分析图片并生成图纸，请稍候…'}</p>}
              {importError && <p className="import-error" role="alert">{importError}</p>}
              <div className="import-actions">
                <button type="button" disabled={importState === 'loading'} onClick={() => setImportOpen(false)}>
                  取消
                </button>
                <button className="primary-button" type="submit" disabled={importState === 'loading' || (importKind === 'image' && user.credits < 1)}>
                  {importState === 'loading' ? '正在处理…' : importKind === 'oxs' ? '免费导入图纸' : '生成图纸 · 1 次'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
      {blankOpen && <BlankProjectDialog onCreate={startBlankProject} onClose={() => setBlankOpen(false)} />}
      {downloadOpen && <DownloadPanel title={document.metadata.title} dirty={dirty}
        onPrepare={prepareDownload} onClose={() => setDownloadOpen(false)} />}
      {accountOpen && <AccountPanel user={user} onClose={() => setAccountOpen(false)} onRefresh={onAccountRefresh} onLogout={async () => {
        if (dirty && !window.confirm('图纸有未保存修改，确定退出登录？')) return
        await onLogout()
      }} />}
    </main></>
  )
}

export default App
