import type { CellSelection } from './types'
import type { CellPosition } from './viewport'

export function normalizeSelection(from: CellPosition, to: CellPosition): CellSelection {
  return {
    startCol: Math.min(from.col, to.col),
    startRow: Math.min(from.row, to.row),
    endCol: Math.max(from.col, to.col),
    endRow: Math.max(from.row, to.row),
  }
}

export function selectionIndices(selection: CellSelection, gridWidth: number): number[] {
  const indices: number[] = []
  for (let row = selection.startRow; row <= selection.endRow; row += 1) {
    for (let col = selection.startCol; col <= selection.endCol; col += 1) {
      indices.push(row * gridWidth + col)
    }
  }
  return indices
}
