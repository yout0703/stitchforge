import { describe, expect, it } from 'vitest'
import { createEditor, editorReducer, floodIndices, isDirty } from './editor'
import { makeDemoPattern } from './demoPattern'

function initial() {
  const document = makeDemoPattern()
  document.grid = { width: 4, height: 3 }
  document.full_stitches.cells = Array(12).fill(1)
  return createEditor(document)
}

describe('editing history', () => {
  it('undoes an entire overlapping stroke and redoes it in one step', () => {
    let state = initial()
    state = editorReducer(state, { type: 'begin', label: '画笔' })
    state = editorReducer(state, { type: 'paint', indices: [0, 1, 2], recipe: 2, label: '画笔' })
    state = editorReducer(state, { type: 'paint', indices: [1, 2, 3], recipe: 2, label: '画笔' })
    state = editorReducer(state, { type: 'end' })
    expect(state.past).toHaveLength(1)
    expect(state.past[0].changes).toHaveLength(4)
    const painted = state.document.full_stitches.cells
    state = editorReducer(state, { type: 'undo' })
    expect(state.document.full_stitches.cells).toEqual(Array(12).fill(1))
    expect(isDirty(state)).toBe(false)
    state = editorReducer(state, { type: 'redo' })
    expect(state.document.full_stitches.cells).toEqual(painted)
    expect(isDirty(state)).toBe(true)
  })

  it('ignores unchanged strokes without losing redo history', () => {
    let state = editorReducer(initial(), { type: 'paint', indices: [2], recipe: 0, label: '留空' })
    state = editorReducer(state, { type: 'undo' })
    state = editorReducer(state, { type: 'begin', label: '画笔' })
    state = editorReducer(state, { type: 'paint', indices: [1], recipe: 1, label: '画笔' })
    state = editorReducer(state, { type: 'end' })
    expect(state.past).toHaveLength(0)
    expect(state.future).toHaveLength(1)
    expect(isDirty(state)).toBe(false)
  })

  it('clears the redo branch on a new edit and can undo a selection as one step', () => {
    let state = editorReducer(initial(), { type: 'paint', indices: [0], recipe: 0, label: '留空' })
    state = editorReducer(state, { type: 'undo' })
    state = editorReducer(state, { type: 'paint', indices: [4, 5, 8, 9], recipe: 2, label: '填充选区' })
    expect(state.future).toHaveLength(0)
    expect(state.past[0].label).toBe('填充选区')
    state = editorReducer(state, { type: 'undo' })
    expect(isDirty(state)).toBe(false)
  })

  it('tracks the saved position through undo/redo without deleting history', () => {
    let state = editorReducer(initial(), { type: 'paint', indices: [0], recipe: 0, label: '留空' })
    state = editorReducer(state, { type: 'saved', document: state.document })
    expect(isDirty(state)).toBe(false)
    state = editorReducer(state, { type: 'undo' })
    expect(isDirty(state)).toBe(true)
    state = editorReducer(state, { type: 'redo' })
    expect(isDirty(state)).toBe(false)
  })

  it('keeps edits made during an asynchronous save dirty', () => {
    let state = editorReducer(initial(), { type: 'paint', indices: [0], recipe: 0, label: '留空' })
    const submitted = state.document
    state = editorReducer(state, { type: 'paint', indices: [1], recipe: 0, label: '留空' })
    state = editorReducer(state, { type: 'saved', document: submitted })
    expect(isDirty(state)).toBe(true)
    expect(state.savedDocument.full_stitches.cells[1]).toBe(1)
  })

  it('resets history when opening another design', () => {
    let state = editorReducer(initial(), { type: 'paint', indices: [0], recipe: 0, label: '留空' })
    state = editorReducer(state, { type: 'reset', document: initial().document })
    expect(state.past).toEqual([])
    expect(state.future).toEqual([])
    expect(isDirty(state)).toBe(false)
  })

  it('retains only the latest 100 operations', () => {
    let state = initial()
    for (let i = 0; i < 120; i++) {
      state = editorReducer(state, { type: 'paint', indices: [0], recipe: i % 2, label: '画笔' })
    }
    expect(state.past).toHaveLength(100)
  })
})

describe('connected fill', () => {
  it('fills four-connected regions without wrapping rows or crossing diagonals', () => {
    expect(floodIndices([1, 2, 1, 1, 2, 1], 3, 0).sort()).toEqual([0, 3])
    expect(floodIndices([1, 2, 2, 1], 2, 0)).toEqual([0])
  })
})

it('protects locked cells and rejects unknown recipes during paint and fill', () => {
  const document = initial().document
  document.regions = [{ id: 'lock', policy: 'locked', spans: [{ row: 0, start_col: 0, end_col: 1 }] }]
  let state = createEditor(document)
  state = editorReducer(state, { type: 'paint', indices: [0, 1], recipe: 2, label: '填充' })
  expect(state.document.full_stitches.cells.slice(0, 2)).toEqual([1, 2])
  const previous = state
  state = editorReducer(state, { type: 'paint', indices: [1], recipe: 999, label: '无效' })
  expect(state).toBe(previous)
})

it('updates counts on overlapping strokes, undo and redo without changing earlier snapshots', () => {
  const original = initial()
  let state = editorReducer(original, { type: 'begin', label: '画笔' })
  state = editorReducer(state, { type: 'paint', indices: [0, 1], recipe: 2, label: '画笔' })
  state = editorReducer(state, { type: 'paint', indices: [1, 2], recipe: 0, label: '画笔' })
  state = editorReducer(state, { type: 'end' })
  expect(state.recipeCounts.get(1)).toBe(9)
  expect(state.recipeCounts.get(2)).toBe(1)
  expect(original.recipeCounts.get(1)).toBe(12)
  state = editorReducer(state, { type: 'undo' })
  expect(state.recipeCounts.get(1)).toBe(12)
  state = editorReducer(state, { type: 'redo' })
  expect(state.recipeCounts.get(1)).toBe(9)
})

it('checks dirty state without scanning the whole grid on hover renders', () => {
  const state = initial()
  const cells = state.document.full_stitches.cells
  state.document.full_stitches.cells = new Proxy(cells, {
    get(target, key, receiver) {
      if (key === 'some' || /^\d+$/.test(String(key))) throw new Error('unexpected full-grid scan')
      return Reflect.get(target, key, receiver)
    },
  })
  expect(isDirty(state)).toBe(false)
})
