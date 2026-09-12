import type { Tool } from './types'

export const BASE_CELL_SIZE = 22
export const MIN_ZOOM = 0.02
export const MAX_ZOOM = 6
export const FIT_PADDING = 28

export interface Viewport {
  zoom: number
  offsetX: number
  offsetY: number
}

export interface CellPosition {
  col: number
  row: number
  index: number
}

export interface CellRange {
  startCol: number
  endCol: number
  startRow: number
  endRow: number
}

interface TemporaryPanShortcut {
  key: string
  code?: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
  editable: boolean
}

export function shouldActivateTemporaryPan(event: TemporaryPanShortcut): boolean {
  const isSpace = event.code === 'Space' || event.key === ' '
  return isSpace && !event.metaKey && !event.ctrlKey && !event.altKey && !event.editable
}

export function shouldStartPan(
  tool: Tool,
  button: number,
  temporaryPan: boolean,
): boolean {
  return tool === 'pan' || button === 1 || temporaryPan
}

export function cellSize(viewport: Viewport): number {
  return BASE_CELL_SIZE * viewport.zoom
}

export function fitViewport(
  canvasWidth: number,
  canvasHeight: number,
  gridWidth: number,
  gridHeight: number,
  padding = FIT_PADDING,
): Viewport {
  if (canvasWidth <= 0 || canvasHeight <= 0 || gridWidth <= 0 || gridHeight <= 0) {
    return { zoom: 1, offsetX: padding, offsetY: padding }
  }
  const availableWidth = Math.max(1, canvasWidth - padding * 2)
  const availableHeight = Math.max(1, canvasHeight - padding * 2)
  const zoom = Math.min(
    MAX_ZOOM,
    Math.max(
      MIN_ZOOM,
      Math.min(
        availableWidth / (gridWidth * BASE_CELL_SIZE),
        availableHeight / (gridHeight * BASE_CELL_SIZE),
      ),
    ),
  )
  const renderedWidth = gridWidth * BASE_CELL_SIZE * zoom
  const renderedHeight = gridHeight * BASE_CELL_SIZE * zoom
  return {
    zoom,
    offsetX: (canvasWidth - renderedWidth) / 2,
    offsetY: (canvasHeight - renderedHeight) / 2,
  }
}

export function screenToCell(
  x: number,
  y: number,
  viewport: Viewport,
  width: number,
  height: number,
): CellPosition | null {
  const size = cellSize(viewport)
  const col = Math.floor((x - viewport.offsetX) / size)
  const row = Math.floor((y - viewport.offsetY) / size)
  if (col < 0 || row < 0 || col >= width || row >= height) return null
  return { col, row, index: row * width + col }
}

export function visibleCellRange(
  viewport: Viewport,
  canvasWidth: number,
  canvasHeight: number,
  gridWidth: number,
  gridHeight: number,
): CellRange | null {
  const size = cellSize(viewport)
  const startCol = Math.max(0, Math.floor(-viewport.offsetX / size))
  const startRow = Math.max(0, Math.floor(-viewport.offsetY / size))
  const endCol = Math.min(gridWidth, Math.ceil((canvasWidth - viewport.offsetX) / size))
  const endRow = Math.min(gridHeight, Math.ceil((canvasHeight - viewport.offsetY) / size))
  if (startCol >= endCol || startRow >= endRow) return null
  return { startCol, endCol, startRow, endRow }
}

export function zoomAt(
  viewport: Viewport,
  factor: number,
  anchorX: number,
  anchorY: number,
): Viewport {
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, viewport.zoom * factor))
  const ratio = zoom / viewport.zoom
  return {
    zoom,
    offsetX: anchorX - (anchorX - viewport.offsetX) * ratio,
    offsetY: anchorY - (anchorY - viewport.offsetY) * ratio,
  }
}

export function lineCellIndices(
  from: CellPosition,
  to: CellPosition,
  gridWidth: number,
): number[] {
  let x = from.col
  let y = from.row
  const dx = Math.abs(to.col - from.col)
  const sx = from.col < to.col ? 1 : -1
  const dy = -Math.abs(to.row - from.row)
  const sy = from.row < to.row ? 1 : -1
  let error = dx + dy
  const indices: number[] = []
  for (;;) {
    indices.push(y * gridWidth + x)
    if (x === to.col && y === to.row) break
    const doubled = 2 * error
    if (doubled >= dy) {
      error += dy
      x += sx
    }
    if (doubled <= dx) {
      error += dx
      y += sy
    }
  }
  return indices
}
