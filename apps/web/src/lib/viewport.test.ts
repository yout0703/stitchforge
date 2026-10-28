import { describe, expect, it } from 'vitest'
import {
  BASE_CELL_SIZE,
  coordinateInterval,
  fitViewport,
  lineCellIndices,
  screenToCell,
  shouldActivateTemporaryPan,
  shouldStartPan,
  visibleCellRange,
  visibleCoordinateValues,
  zoomAt,
  type CellPosition,
} from './viewport'

describe('screenToCell', () => {
  it('maps top-left and bottom-right without mirroring', () => {
    const viewport = { zoom: 1, offsetX: 10, offsetY: 20 }
    expect(screenToCell(10, 20, viewport, 4, 3)).toEqual({ col: 0, row: 0, index: 0 })
    expect(screenToCell(10 + BASE_CELL_SIZE * 3.9, 20 + BASE_CELL_SIZE * 2.9, viewport, 4, 3))
      .toEqual({ col: 3, row: 2, index: 11 })
    expect(screenToCell(10 + BASE_CELL_SIZE * 4, 20, viewport, 4, 3)).toBeNull()
  })
})

describe('viewport', () => {
  it('fits a tall large design entirely inside the canvas', () => {
    const canvasWidth = 1200
    const canvasHeight = 760
    const viewport = fitViewport(canvasWidth, canvasHeight, 220, 330)
    const renderedWidth = 220 * BASE_CELL_SIZE * viewport.zoom
    const renderedHeight = 330 * BASE_CELL_SIZE * viewport.zoom
    expect(viewport.zoom).toBeLessThan(0.25)
    expect(viewport.offsetX).toBeGreaterThanOrEqual(0)
    expect(viewport.offsetY).toBeGreaterThanOrEqual(0)
    expect(viewport.offsetX + renderedWidth).toBeLessThanOrEqual(canvasWidth)
    expect(viewport.offsetY + renderedHeight).toBeLessThanOrEqual(canvasHeight)
  })

  it('centers a fitted design in the canvas', () => {
    const viewport = fitViewport(1000, 700, 100, 100)
    const renderedSize = 100 * BASE_CELL_SIZE * viewport.zoom
    expect(viewport.offsetX).toBeCloseTo((1000 - renderedSize) / 2)
    expect(viewport.offsetY).toBeCloseTo((700 - renderedSize) / 2)
  })

  it('keeps the world point under the zoom anchor fixed', () => {
    const before = { zoom: 1, offsetX: 30, offsetY: 40 }
    const after = zoomAt(before, 2, 300, 200)
    const worldXBefore = (300 - before.offsetX) / (BASE_CELL_SIZE * before.zoom)
    const worldXAfter = (300 - after.offsetX) / (BASE_CELL_SIZE * after.zoom)
    const worldYBefore = (200 - before.offsetY) / (BASE_CELL_SIZE * before.zoom)
    const worldYAfter = (200 - after.offsetY) / (BASE_CELL_SIZE * after.zoom)
    expect(worldXAfter).toBeCloseTo(worldXBefore)
    expect(worldYAfter).toBeCloseTo(worldYBefore)
  })

  it('culls a 300 by 300 design to the visible canvas', () => {
    const range = visibleCellRange(
      { zoom: 1, offsetX: -2200, offsetY: -3300 },
      1000,
      700,
      300,
      300,
    )
    expect(range).toEqual({ startCol: 100, endCol: 146, startRow: 150, endRow: 182 })
  })
})

describe('coordinate rulers', () => {
  it('hides labels when cells are too small and adapts density when readable', () => {
    expect(coordinateInterval(4)).toBeNull()
    expect(coordinateInterval(7)).toBe(10)
    expect(coordinateInterval(12)).toBe(5)
    expect(coordinateInterval(56)).toBe(1)
  })

  it('returns one-based labels for the visible range without crowding the last edge', () => {
    expect(visibleCoordinateValues(0, 42, 100, 10)).toEqual([1, 10, 20, 30, 40])
    expect(visibleCoordinateValues(20, 65, 100, 10)).toEqual([30, 40, 50, 60])
    expect(visibleCoordinateValues(0, 100, 100, 20)).toEqual([1, 20, 40, 60, 80, 100])
    expect(visibleCoordinateValues(0, 154, 154, 10)).toEqual([1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150])
  })
})

describe('temporary space pan', () => {
  it('activates for an unmodified space press outside an editor', () => {
    expect(
      shouldActivateTemporaryPan({
        key: ' ',
        code: 'Space',
        metaKey: false,
        ctrlKey: false,
        altKey: false,
        editable: false,
      }),
    ).toBe(true)
  })

  it('does not steal space from form fields or modified shortcuts', () => {
    const base = {
      key: ' ',
      code: 'Space',
      metaKey: false,
      ctrlKey: false,
      altKey: false,
      editable: false,
    }
    expect(shouldActivateTemporaryPan({ ...base, editable: true })).toBe(false)
    expect(shouldActivateTemporaryPan({ ...base, ctrlKey: true })).toBe(false)
  })

  it('starts panning for space, the pan tool, or the middle mouse button', () => {
    expect(shouldStartPan('paint', 0, true)).toBe(true)
    expect(shouldStartPan('pan', 0, false)).toBe(true)
    expect(shouldStartPan('paint', 1, false)).toBe(true)
    expect(shouldStartPan('paint', 0, false)).toBe(false)
  })
})

describe('lineCellIndices', () => {
  const cell = (col: number, row: number): CellPosition => ({ col, row, index: row * 10 + col })

  it('fills every crossed cell in a fast diagonal drag', () => {
    expect(lineCellIndices(cell(1, 1), cell(5, 3), 10)).toEqual([11, 22, 23, 34, 35])
  })

  it('works in reverse and includes both endpoints', () => {
    expect(lineCellIndices(cell(3, 3), cell(3, 0), 10)).toEqual([33, 23, 13, 3])
  })
})

it('keeps the pattern point under the moving pinch midpoint', async () => {
  const { pinchViewport } = await import('./viewport')
  const viewport = { zoom: 1, offsetX: 10, offsetY: 20 }
  const result = pinchViewport(viewport, [{ x: 100, y: 100 }, { x: 200, y: 100 }], [{ x: 80, y: 130 }, { x: 280, y: 130 }])
  expect(result.zoom).toBe(2)
  expect((150 - viewport.offsetX) * 2 + result.offsetX).toBe(180)
  expect((100 - viewport.offsetY) * 2 + result.offsetY).toBe(130)
})
