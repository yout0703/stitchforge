import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent } from 'react'
import { recipeSymbols } from '../lib/symbols'
import { floodIndices } from '../lib/editor'
import { StrokeBatch } from '../lib/strokeBatch'
import { normalizeSelection } from '../lib/selection'
import type { CellSelection, PatternDocument, Tool } from '../lib/types'
import {
  cellSize,
  pinchViewport,
  type TouchPoint,
  fitViewport,
  lineCellIndices,
  screenToCell,
  shouldActivateTemporaryPan,
  shouldStartPan,
  visibleCellRange,
  zoomAt,
  type CellPosition,
  type Viewport,
} from '../lib/viewport'

interface PatternCanvasProps {
  document: PatternDocument
  readOnly: boolean
  highlightedRecipe: number | null
  showSymbols: boolean
  showGrid: boolean
  tool: Tool
  selectedRecipe: number
  selection: CellSelection | null
  onEditBegin: (label: string) => void
  onEditEnd: () => void
  onPaint: (indices: readonly number[], recipeIndex: number) => void
  onPick: (index: number) => void
  onSelect: (selection: CellSelection) => void
  onHover: (cell: { col: number; row: number } | null) => void
}

interface Gesture {
  pointerId: number
  kind: 'pan' | 'paint' | 'pending-paint' | 'select'
  x: number
  y: number
  lastCell: CellPosition | null
  recipe?: number
  label?: string
  fill?: boolean
  distance?: number
}

function pointerPosition(event: PointerEvent<HTMLCanvasElement>) {
  const rect = event.currentTarget.getBoundingClientRect()
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

export function PatternCanvas({
  document,
  readOnly,
  highlightedRecipe,
  showSymbols,
  showGrid,
  tool,
  selectedRecipe,
  selection,
  onPaint: commitPaint,
  onEditBegin,
  onEditEnd: finishEdit,
  onPick,
  onSelect,
  onHover,
}: PatternCanvasProps) {
  const batch = useMemo(() => new StrokeBatch(commitPaint), [commitPaint])
  useEffect(() => () => batch.discard(), [batch])
  const onPaint = (indices: readonly number[], recipe: number) => batch.add(indices, recipe)
  const onEditEnd = useCallback(() => { batch.flush(); finishEdit() }, [batch, finishEdit])
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const editEndRef = useRef(onEditEnd)
  useEffect(() => { editEndRef.current = onEditEnd }, [onEditEnd])
  const gestureRef = useRef<Gesture | null>(null)
  const touchesRef = useRef(new Map<number, TouchPoint>())
  const temporaryPanRef = useRef(false)
  const [surface, setSurface] = useState({ width: 0, height: 0, dpr: 1 })
  const [viewport, setViewport] = useState<Viewport>({ zoom: 1, offsetX: 48, offsetY: 44 })
  const [temporaryPan, setTemporaryPan] = useState(false)
  const [panning, setPanning] = useState(false)
  const [canvasFocused, setCanvasFocused] = useState(false)
  const [keyboardCell, setKeyboardCell] = useState({ col: 0, row: 0 })
  const symbols = useMemo(() => recipeSymbols({ recipes: document.recipes, legend: document.legend }), [document.recipes, document.legend])
  const colors = useMemo(() => {
    const materials = new Map(document.materials.map((item) => [item.id, item]))
    return new Map(
      document.recipes.map((recipe) => [
        recipe.index,
        materials.get(recipe.components[0]?.material_id ?? '')?.color.display_srgb ?? '#9BA2AE',
      ]),
    )
  }, [document.materials, document.recipes])

  useEffect(() => {
    function isEditableTarget(target: EventTarget | null) {
      return (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      )
    }

    function handleKeyDown(event: KeyboardEvent) {
      // A canvas shortcut must not consume Space on toolbar buttons or dialogs.
      if (event.target !== canvasRef.current) return
      if (
        !shouldActivateTemporaryPan({
          key: event.key,
          code: event.code,
          metaKey: event.metaKey,
          ctrlKey: event.ctrlKey,
          altKey: event.altKey,
          editable: isEditableTarget(event.target),
        })
      ) return
      event.preventDefault()
      if (temporaryPanRef.current) return
      temporaryPanRef.current = true
      setTemporaryPan(true)
    }

    function clearTemporaryPan(event?: KeyboardEvent) {
      if (event && event.code !== 'Space' && event.key !== ' ') return
      if (!temporaryPanRef.current) return
      if (event?.target === canvasRef.current) event?.preventDefault()
      temporaryPanRef.current = false
      setTemporaryPan(false)
    }

    function handleWindowBlur() {
      clearTemporaryPan()
      if (gestureRef.current?.kind === 'paint') editEndRef.current()
      gestureRef.current = null
      setPanning(false)
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', clearTemporaryPan)
    window.addEventListener('blur', handleWindowBlur)
    return () => {
      if (gestureRef.current?.kind === 'paint') editEndRef.current()
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', clearTemporaryPan)
      window.removeEventListener('blur', handleWindowBlur)
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    let animationFrame = 0
    let previousSize: { width: number; height: number } | null = null
    const observer = new ResizeObserver(([entry]) => {
      cancelAnimationFrame(animationFrame)
      // Mobile source comparison hides this pane. Do not fit a hidden 0×0
      // canvas or discard the reader's current position when it becomes visible.
      const width = Math.floor(entry.contentRect.width)
      const height = Math.floor(entry.contentRect.height)
      if (width <= 0 || height <= 0) return
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      animationFrame = requestAnimationFrame(() => {
        const pixelWidth = Math.floor(width * dpr)
        const pixelHeight = Math.floor(height * dpr)
        if (canvas.width !== pixelWidth) canvas.width = pixelWidth
        if (canvas.height !== pixelHeight) canvas.height = pixelHeight
        const previous = previousSize
        setViewport((current) => previous
          ? {
              ...current,
              offsetX: current.offsetX + (width - previous.width) / 2,
              offsetY: current.offsetY + (height - previous.height) / 2,
            }
          : fitViewport(width, height, document.grid.width, document.grid.height))
        previousSize = { width, height }
        setSurface((current) =>
          current.width === width && current.height === height && current.dpr === dpr
            ? current
            : { width, height, dpr },
        )
      })
    })
    observer.observe(container)
    return () => {
      cancelAnimationFrame(animationFrame)
      observer.disconnect()
    }
  }, [document.grid.height, document.grid.width])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || surface.width === 0 || surface.height === 0) return
    const context = canvas.getContext('2d')
    if (!context) return
    context.setTransform(surface.dpr, 0, 0, surface.dpr, 0, 0)
    context.clearRect(0, 0, surface.width, surface.height)
    context.fillStyle = '#C8CDD4'
    context.fillRect(0, 0, surface.width, surface.height)

    const size = cellSize(viewport)
    const range = visibleCellRange(
      viewport,
      surface.width,
      surface.height,
      document.grid.width,
      document.grid.height,
    )
    if (!range) return

    context.save()
    context.translate(viewport.offsetX, viewport.offsetY)
    context.fillStyle = document.fabric.display_srgb
    context.fillRect(0, 0, document.grid.width * size, document.grid.height * size)

    for (let row = range.startRow; row < range.endRow; row += 1) {
      for (let col = range.startCol; col < range.endCol; col += 1) {
        const recipe = document.full_stitches.cells[row * document.grid.width + col]
        if (recipe === 0) continue
        const x = col * size
        const y = row * size
        const inset = size >= 4 ? 1 : 0
        context.globalAlpha = highlightedRecipe !== null && recipe !== highlightedRecipe ? 0.15 : 1
        context.fillStyle = colors.get(recipe) ?? '#9BA2AE'
        context.fillRect(
          x + inset,
          y + inset,
          Math.max(0.5, size - inset * 2),
          Math.max(0.5, size - inset * 2),
        )
        if (showSymbols && size >= 9) {
          const color = colors.get(recipe) ?? '#000000'
          const rgb = color.replace('#', '')
          const brightness = parseInt(rgb.slice(0, 2), 16) * 0.299 + parseInt(rgb.slice(2, 4), 16) * 0.587 + parseInt(rgb.slice(4, 6), 16) * 0.114
          context.fillStyle = brightness > 140 ? '#111827' : '#FFFFFF'
          context.font = `bold ${Math.max(8, size * 0.6)}px monospace`
          context.textAlign = 'center'
          context.textBaseline = 'middle'
          context.fillText(symbols.get(recipe) ?? '?', x + size / 2, y + size / 2)
        } else if (size >= 9) {
          context.strokeStyle = recipe === 1 ? '#FFFFFF' : 'rgba(20, 27, 38, .5)'
          context.lineWidth = Math.max(1, size * 0.08)
          context.beginPath()
          context.moveTo(x + size * 0.22, y + size * 0.22)
          context.lineTo(x + size * 0.78, y + size * 0.78)
          context.moveTo(x + size * 0.78, y + size * 0.22)
          context.lineTo(x + size * 0.22, y + size * 0.78)
          context.stroke()
        }
      }
    }

    context.globalAlpha = 1
    context.lineWidth = 1
    for (let col = range.startCol; col <= range.endCol; col += 1) {
      if (!showGrid || (size < 5 && col % 10 !== 0)) continue
      context.strokeStyle = col % 10 === 0 ? 'rgba(31, 42, 58, .55)' : 'rgba(31, 42, 58, .18)'
      context.beginPath()
      context.moveTo(col * size + 0.5, range.startRow * size)
      context.lineTo(col * size + 0.5, range.endRow * size)
      context.stroke()
    }
    for (let row = range.startRow; row <= range.endRow; row += 1) {
      if (!showGrid || (size < 5 && row % 10 !== 0)) continue
      context.strokeStyle = row % 10 === 0 ? 'rgba(31, 42, 58, .55)' : 'rgba(31, 42, 58, .18)'
      context.beginPath()
      context.moveTo(range.startCol * size, row * size + 0.5)
      context.lineTo(range.endCol * size, row * size + 0.5)
      context.stroke()
    }
    if (selection) {
      const x = selection.startCol * size
      const y = selection.startRow * size
      const width = (selection.endCol - selection.startCol + 1) * size
      const height = (selection.endRow - selection.startRow + 1) * size
      context.fillStyle = 'rgba(35, 92, 207, .12)'
      context.fillRect(x, y, width, height)
      context.strokeStyle = '#235CCF'
      context.lineWidth = 2
      context.setLineDash([5, 3])
      context.strokeRect(x + 1, y + 1, width - 2, height - 2)
      context.setLineDash([])
    }
    if (canvasFocused) {
      const x = keyboardCell.col * size
      const y = keyboardCell.row * size
      context.strokeStyle = '#98472f'
      context.lineWidth = Math.max(2, Math.min(4, size * 0.16))
      context.strokeRect(x + 1, y + 1, Math.max(1, size - 2), Math.max(1, size - 2))
    }
    context.restore()
  }, [canvasFocused, colors, document, keyboardCell, selection, surface, viewport, highlightedRecipe, showSymbols, showGrid, symbols])

  function cellAt(event: PointerEvent<HTMLCanvasElement>) {
    const position = pointerPosition(event)
    return screenToCell(
      position.x,
      position.y,
      viewport,
      document.grid.width,
      document.grid.height,
    )
  }

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    const position = pointerPosition(event)
    if (event.button !== 0 && event.button !== 1) return
    if (event.pointerType === 'touch') {
      touchesRef.current.set(event.pointerId, position)
      if (touchesRef.current.size > 1) {
        event.preventDefault()
        event.currentTarget.setPointerCapture(event.pointerId)
        if (gestureRef.current?.kind === 'paint') onEditEnd()
        gestureRef.current = null
        setPanning(true)
        return
      }
    }
    const pan = readOnly || shouldStartPan(tool, event.button, temporaryPanRef.current)
    if (!pan && event.button !== 0) return
    event.preventDefault()
    event.currentTarget.focus({ preventScroll: true })
    event.currentTarget.setPointerCapture(event.pointerId)
    if (pan) {
      gestureRef.current = {
        pointerId: event.pointerId,
        kind: 'pan',
        ...position,
        lastCell: readOnly && event.button === 0 && !temporaryPanRef.current ? cellAt(event) : null,
      }
      setPanning(true)
      return
    }
    const cell = cellAt(event)
    if (!cell) return
    if (tool === 'pick') {
      onPick(cell.index)
      return
    }
    if (tool === 'select') {
      onSelect(normalizeSelection(cell, cell))
      gestureRef.current = {
        pointerId: event.pointerId,
        kind: 'select',
        ...position,
        lastCell: cell,
      }
      return
    }
    const recipe = tool === 'erase' ? 0 : selectedRecipe
    if (tool === 'fill') {
      if (event.pointerType === 'touch') {
        gestureRef.current = {
          pointerId: event.pointerId,
          kind: 'pending-paint',
          recipe,
          label: '连通填色',
          fill: true,
          ...position,
          lastCell: cell,
        }
        return
      }
      onEditBegin('连通填色')
      onPaint(floodIndices(document.full_stitches.cells, document.grid.width, cell.index), recipe)
      onEditEnd()
      return
    }
    if (event.pointerType === 'touch') {
      gestureRef.current = {
        pointerId: event.pointerId,
        kind: 'pending-paint',
        recipe,
        label: tool === 'erase' ? '橡皮擦' : '画笔',
        ...position,
        lastCell: cell,
      }
      return
    }
    onEditBegin(tool === 'erase' ? '橡皮擦' : '画笔')
    onPaint([cell.index], recipe)
    gestureRef.current = {
      pointerId: event.pointerId,
      kind: 'paint',
      recipe,
      ...position,
      lastCell: cell,
    }
  }

  function handlePointerMove(event: PointerEvent<HTMLCanvasElement>) {
    const position = pointerPosition(event)
    if (touchesRef.current.has(event.pointerId)) {
      const before = [...touchesRef.current.values()]
      touchesRef.current.set(event.pointerId, position)
      if (touchesRef.current.size >= 2) {
        const after = [...touchesRef.current.values()]
        setViewport(current => pinchViewport(current, [before[0], before[1]], [after[0], after[1]]))
        return
      }
    }
    const cell = cellAt(event)
    onHover(cell ? { col: cell.col, row: cell.row } : null)
    const gesture = gestureRef.current
    if (!gesture || gesture.pointerId !== event.pointerId) return
    if (gesture.kind === 'pending-paint') {
      if (gesture.fill || !cell || !gesture.lastCell || Math.hypot(position.x - gesture.x, position.y - gesture.y) <= 5) return
      onEditBegin(gesture.label ?? '画笔')
      onPaint(lineCellIndices(gesture.lastCell, cell, document.grid.width), gesture.recipe ?? selectedRecipe)
      gesture.kind = 'paint'
      gesture.lastCell = cell
    } else if (gesture.kind === 'pan') {
      // Capture deltas before mutating the gesture: React may run the updater later.
      const deltaX = position.x - gesture.x
      const deltaY = position.y - gesture.y
      gesture.distance = (gesture.distance ?? 0) + Math.hypot(deltaX, deltaY)
      if (gesture.distance > 6) gesture.lastCell = null
      setViewport((current) => ({
        ...current,
        offsetX: current.offsetX + deltaX,
        offsetY: current.offsetY + deltaY,
      }))
      gesture.x = position.x
      gesture.y = position.y
    } else if (
      gesture.kind === 'select' &&
      cell &&
      gesture.lastCell &&
      cell.index !== gesture.lastCell.index
    ) {
      onSelect(normalizeSelection(gesture.lastCell, cell))
    } else if (gesture.kind === 'paint' && cell && gesture.lastCell && cell.index !== gesture.lastCell.index) {
      onPaint(lineCellIndices(gesture.lastCell, cell, document.grid.width), gesture.recipe ?? selectedRecipe)
      gesture.lastCell = cell
    }
  }

  function handlePointerEnd(event: PointerEvent<HTMLCanvasElement>) {
    const wasPinching = touchesRef.current.size > 1
    touchesRef.current.delete(event.pointerId)
    if (wasPinching) {
      const remaining = [...touchesRef.current.entries()][0]
      gestureRef.current = remaining ? { pointerId: remaining[0], kind: 'pan', ...remaining[1], lastCell: null } : null
      setPanning(Boolean(remaining))
      return
    }
    if (gestureRef.current?.pointerId === event.pointerId) {
      const wasPanning = gestureRef.current.kind === 'pan'
      if (event.type === 'pointerup' && readOnly && wasPanning && gestureRef.current.lastCell) onPick(gestureRef.current.lastCell.index)
      if (event.type === 'pointerup' && gestureRef.current.kind === 'pending-paint' && gestureRef.current.lastCell) {
        const gesture = gestureRef.current
        const lastCell = gesture.lastCell!
        onEditBegin(gesture.label ?? '画笔')
        onPaint(
          gesture.fill
            ? floodIndices(document.full_stitches.cells, document.grid.width, lastCell.index)
            : [lastCell.index],
          gesture.recipe ?? selectedRecipe,
        )
        onEditEnd()
      }
      if (gestureRef.current.kind === 'paint') onEditEnd()
      gestureRef.current = null
      if (wasPanning) setPanning(false)
    }
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    function handleWheel(event: globalThis.WheelEvent) {
      event.preventDefault()
      const rect = canvas!.getBoundingClientRect()
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1)
      setViewport((current) =>
        zoomAt(current, Math.exp(-delta * 0.0015), event.clientX - rect.left, event.clientY - rect.top),
      )
    }
    // React's delegated wheel listener is passive; cancellation must be native.
    canvas.addEventListener('wheel', handleWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', handleWheel)
  }, [])

  function revealKeyboardCell(cell: { col: number; row: number }) {
    setViewport((current) => {
      const size = cellSize(current)
      const padding = Math.min(48, Math.max(18, size))
      const left = current.offsetX + cell.col * size
      const top = current.offsetY + cell.row * size
      let offsetX = current.offsetX
      let offsetY = current.offsetY
      if (left < padding) offsetX += padding - left
      else if (left + size > surface.width - padding) offsetX -= left + size - (surface.width - padding)
      if (top < padding) offsetY += padding - top
      else if (top + size > surface.height - padding) offsetY -= top + size - (surface.height - padding)
      return offsetX === current.offsetX && offsetY === current.offsetY ? current : { ...current, offsetX, offsetY }
    })
  }

  function handleCanvasKeyDown(event: ReactKeyboardEvent<HTMLCanvasElement>) {
    const movement = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    }[event.key] as [number, number] | undefined
    if (movement) {
      event.preventDefault()
      event.stopPropagation()
      const next = {
        col: Math.max(0, Math.min(document.grid.width - 1, keyboardCell.col + movement[0])),
        row: Math.max(0, Math.min(document.grid.height - 1, keyboardCell.row + movement[1])),
      }
      setKeyboardCell(next)
      onHover(next)
      revealKeyboardCell(next)
      return
    }
    // In edit mode Space remains the established temporary-pan shortcut.
    if (event.key !== 'Enter' && (event.key !== ' ' || !readOnly)) return
    event.preventDefault()
    event.stopPropagation()
    const index = keyboardCell.row * document.grid.width + keyboardCell.col
    if (readOnly || tool === 'pick' || tool === 'pan') {
      onPick(index)
      return
    }
    if (tool === 'select') {
      onSelect({ startCol: keyboardCell.col, startRow: keyboardCell.row, endCol: keyboardCell.col, endRow: keyboardCell.row })
      return
    }
    const recipe = tool === 'erase' ? 0 : selectedRecipe
    onEditBegin(tool === 'fill' ? '连通填色' : tool === 'erase' ? '橡皮擦' : '画笔')
    onPaint(tool === 'fill' ? floodIndices(document.full_stitches.cells, document.grid.width, index) : [index], recipe)
    onEditEnd()
  }

  return (
    <div ref={containerRef} className="canvas-viewport">
      <canvas
        ref={canvasRef}
        className={`pattern-canvas tool-${readOnly ? 'pan' : tool}${temporaryPan ? ' temporary-pan' : ''}${panning ? ' is-panning' : ''}`}
        aria-label={readOnly ? "十字绣图纸，拖动平移，双指缩放，点击查看线色" : "可编辑十字绣针格"}
        aria-describedby="pattern-canvas-help"
        tabIndex={0}
        onFocus={() => { setCanvasFocused(true); onHover(keyboardCell) }}
        onBlur={() => {
          setCanvasFocused(false)
          onHover(null)
          temporaryPanRef.current = false
          setTemporaryPan(false)
        }}
        onKeyDown={handleCanvasKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onLostPointerCapture={handlePointerEnd}
        onPointerLeave={() => onHover(null)}
      />
      <p className="sr-only" id="pattern-canvas-help" aria-live="polite">使用方向键移动针格，{readOnly ? '按 Enter 或空格查看当前线色' : '按 Enter 执行当前工具，按住空格拖动图纸'}。当前第 {keyboardCell.col + 1} 列，第 {keyboardCell.row + 1} 行。</p>
    </div>
  )
}
