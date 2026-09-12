import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent } from 'react'
import { recipeSymbols } from '../lib/symbols'
import { floodIndices } from '../lib/editor'
import { normalizeSelection } from '../lib/selection'
import type { CellSelection, PatternDocument, Tool } from '../lib/types'
import {
  cellSize,
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
  kind: 'pan' | 'paint' | 'select'
  x: number
  y: number
  lastCell: CellPosition | null
  recipe?: number
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
  onPaint,
  onEditBegin,
  onEditEnd,
  onPick,
  onSelect,
  onHover,
}: PatternCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const editEndRef = useRef(onEditEnd)
  useEffect(() => { editEndRef.current = onEditEnd }, [onEditEnd])
  const gestureRef = useRef<Gesture | null>(null)
  const temporaryPanRef = useRef(false)
  const [surface, setSurface] = useState({ width: 0, height: 0, dpr: 1 })
  const [viewport, setViewport] = useState<Viewport>({ zoom: 1, offsetX: 48, offsetY: 44 })
  const [temporaryPan, setTemporaryPan] = useState(false)
  const [panning, setPanning] = useState(false)
  const symbols = useMemo(() => recipeSymbols(document), [document])
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
      event?.preventDefault()
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
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(1, Math.floor(entry.contentRect.width))
      const height = Math.max(1, Math.floor(entry.contentRect.height))
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      cancelAnimationFrame(animationFrame)
      animationFrame = requestAnimationFrame(() => {
        const pixelWidth = Math.floor(width * dpr)
        const pixelHeight = Math.floor(height * dpr)
        if (canvas.width !== pixelWidth) canvas.width = pixelWidth
        if (canvas.height !== pixelHeight) canvas.height = pixelHeight
        setViewport(fitViewport(width, height, document.grid.width, document.grid.height))
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
    context.restore()
  }, [colors, document, selection, surface, viewport, highlightedRecipe, showSymbols, showGrid, symbols])

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
      onEditBegin('连通填色')
      onPaint(floodIndices(document.full_stitches.cells, document.grid.width, cell.index), recipe)
      onEditEnd()
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
    const cell = cellAt(event)
    onHover(cell ? { col: cell.col, row: cell.row } : null)
    const gesture = gestureRef.current
    if (!gesture || gesture.pointerId !== event.pointerId) return
    if (gesture.kind === 'pan') {
      // Capture deltas before mutating the gesture: React may run the updater later.
      const deltaX = position.x - gesture.x
      const deltaY = position.y - gesture.y
      if (Math.abs(deltaX) + Math.abs(deltaY) > 0) gesture.lastCell = null
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
    if (gestureRef.current?.pointerId === event.pointerId) {
      const wasPanning = gestureRef.current.kind === 'pan'
      if (readOnly && wasPanning && gestureRef.current.lastCell) onPick(gestureRef.current.lastCell.index)
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

  return (
    <div ref={containerRef} className="canvas-viewport">
      <canvas
        ref={canvasRef}
        className={`pattern-canvas tool-${readOnly ? 'pan' : tool}${temporaryPan ? ' temporary-pan' : ''}${panning ? ' is-panning' : ''}`}
        aria-label="可编辑十字绣针格"
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onLostPointerCapture={handlePointerEnd}
        onPointerLeave={() => onHover(null)}
      />
    </div>
  )
}
