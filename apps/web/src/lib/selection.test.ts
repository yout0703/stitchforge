import { describe, expect, it } from 'vitest'
import { normalizeSelection, selectionIndices } from './selection'

describe('selection', () => {
  it('normalizes reverse drag bounds', () => {
    expect(
      normalizeSelection(
        { col: 5, row: 4, index: 45 },
        { col: 2, row: 1, index: 12 },
      ),
    ).toEqual({ startCol: 2, startRow: 1, endCol: 5, endRow: 4 })
  })

  it('enumerates each selected cell once in row-major order', () => {
    expect(
      selectionIndices({ startCol: 1, startRow: 1, endCol: 2, endRow: 2 }, 5),
    ).toEqual([6, 7, 11, 12])
  })
})
