import type { PatternDocument } from './types'

interface CellChange { index: number; before: number; after: number }
export interface Edit { label: string; changes: CellChange[] }
export interface EditorState {
  document: PatternDocument
  savedDocument: PatternDocument
  past: Edit[]
  future: Edit[]
  active: { label: string; changes: Map<number, CellChange> } | null
}
export type EditorAction =
  | { type: 'reset'; document: PatternDocument }
  | { type: 'saved'; document: PatternDocument }
  | { type: 'begin'; label: string }
  | { type: 'paint'; indices: readonly number[]; recipe: number; label: string }
  | { type: 'end' | 'undo' | 'redo' }

export function createEditor(document: PatternDocument): EditorState {
  return { document, savedDocument: document, past: [], future: [], active: null }
}

export function isDirty(state: EditorState): boolean {
  const cells = state.document.full_stitches.cells
  const saved = state.savedDocument.full_stitches.cells
  return cells.length !== saved.length || cells.some((value, index) => value !== saved[index])
}

function finish(state: EditorState): EditorState {
  if (!state.active) return state
  const changes = [...state.active.changes.values()].filter((c) => c.before !== c.after)
  return {
    ...state,
    active: null,
    past: changes.length ? [...state.past, { label: state.active.label, changes }].slice(-100) : state.past,
    future: changes.length ? [] : state.future,
  }
}

function applyChanges(document: PatternDocument, changes: CellChange[], direction: 'before' | 'after') {
  const cells = document.full_stitches.cells.slice()
  for (const change of changes) cells[change.index] = change[direction]
  return { ...document, full_stitches: { ...document.full_stitches, cells } }
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'reset': return createEditor(action.document)
    // Save the exact submitted snapshot; edits made while the request runs remain dirty.
    case 'saved': return { ...state, savedDocument: action.document }
    case 'begin': return { ...finish(state), active: { label: action.label, changes: new Map() } }
    case 'end': return finish(state)
    case 'paint': {
      const active = state.active ?? { label: action.label, changes: new Map<number, CellChange>() }
      const changes = new Map(active.changes)
      const cells = state.document.full_stitches.cells.slice()
      let changed = false
      for (const index of action.indices) {
        if (!Number.isInteger(index) || index < 0 || index >= cells.length || cells[index] === action.recipe) continue
        changes.set(index, { index, before: changes.get(index)?.before ?? cells[index], after: action.recipe })
        cells[index] = action.recipe
        changed = true
      }
      if (!changed) return state
      const next = {
        ...state,
        document: { ...state.document, full_stitches: { ...state.document.full_stitches, cells } },
        active: { label: active.label, changes },
      }
      return state.active ? next : finish(next)
    }
    case 'undo': {
      const current = finish(state)
      const edit = current.past.at(-1)
      if (!edit) return current
      return { ...current, document: applyChanges(current.document, edit.changes, 'before'),
        past: current.past.slice(0, -1), future: [...current.future, edit] }
    }
    case 'redo': {
      const current = finish(state)
      const edit = current.future.at(-1)
      if (!edit) return current
      return { ...current, document: applyChanges(current.document, edit.changes, 'after'),
        past: [...current.past, edit], future: current.future.slice(0, -1) }
    }
  }
}

export function floodIndices(cells: readonly number[], width: number, start: number): number[] {
  if (start < 0 || start >= cells.length) return []
  const color = cells[start]
  const seen = new Set([start])
  const queue = [start]
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const index = queue[cursor]
    const neighbors = [index - width, index + width]
    if (index % width > 0) neighbors.push(index - 1)
    if (index % width < width - 1) neighbors.push(index + 1)
    for (const neighbor of neighbors) {
      if (neighbor < 0 || neighbor >= cells.length || seen.has(neighbor) || cells[neighbor] !== color) continue
      seen.add(neighbor)
      queue.push(neighbor)
    }
  }
  return queue
}
