import type { PatternDocument } from './types'

export interface CellChange { index: number; before: number; after: number }
export interface Edit { label: string; changes: CellChange[] }
export interface EditorState {
  document: PatternDocument
  savedDocument: PatternDocument
  past: Edit[]
  future: Edit[]
  dirtyCount: number
  recipeCounts: Map<number, number>
  lockedIndices: Set<number>
  active: { label: string; changes: Map<number, CellChange> } | null
}
export type EditorAction =
  | { type: 'reset'; document: PatternDocument }
  | { type: 'restore'; state: EditorState }
  | { type: 'saved'; document: PatternDocument }
  | { type: 'begin'; label: string }
  | { type: 'paint'; indices: readonly number[]; recipe: number; label: string }
  | { type: 'end' | 'undo' | 'redo' }

function countDirty(document: PatternDocument, saved: PatternDocument): number {
  const cells = document.full_stitches.cells
  const before = saved.full_stitches.cells
  let count = Math.abs(cells.length - before.length)
  for (let index = 0; index < Math.min(cells.length, before.length); index++) {
    if (cells[index] !== before[index]) count++
  }
  return count
}

export function createEditor(document: PatternDocument, savedDocument = document): EditorState {
  const recipeCounts = new Map<number, number>()
  for (const recipe of document.full_stitches.cells) {
    if (recipe > 0) recipeCounts.set(recipe, (recipeCounts.get(recipe) ?? 0) + 1)
  }
  const lockedIndices = new Set<number>()
  for (const region of document.regions ?? []) {
    if (region.policy !== 'locked') continue
    for (const span of region.spans) {
      for (let col = span.start_col; col < span.end_col; col++) {
        lockedIndices.add(span.row * document.grid.width + col)
      }
    }
  }
  return { document, savedDocument, recipeCounts, lockedIndices,
    dirtyCount: document === savedDocument ? 0 : countDirty(document, savedDocument),
    past: [], future: [], active: null }
}

export function isDirty(state: EditorState): boolean {
  return state.dirtyCount > 0
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

function applyChanges(state: EditorState, changes: CellChange[], direction: 'before' | 'after') {
  const cells = state.document.full_stitches.cells.slice()
  const recipeCounts = new Map(state.recipeCounts)
  let dirtyCount = state.dirtyCount
  for (const change of changes) {
    const before = cells[change.index]
    const after = change[direction]
    if (before === after) continue
    const saved = state.savedDocument.full_stitches.cells[change.index]
    dirtyCount += Number(after !== saved) - Number(before !== saved)
    if (before > 0) {
      const remaining = (recipeCounts.get(before) ?? 0) - 1
      if (remaining) recipeCounts.set(before, remaining)
      else recipeCounts.delete(before)
    }
    if (after > 0) recipeCounts.set(after, (recipeCounts.get(after) ?? 0) + 1)
    cells[change.index] = after
  }
  return { ...state, dirtyCount, recipeCounts,
    document: { ...state.document, full_stitches: { ...state.document.full_stitches, cells } } }
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'reset': return createEditor(action.document)
    case 'restore': return action.state
    // Save the exact submitted snapshot; edits made while the request runs remain dirty.
    case 'saved': return { ...state, savedDocument: action.document, dirtyCount: countDirty(state.document, action.document) }
    case 'begin': return { ...finish(state), active: { label: action.label, changes: new Map() } }
    case 'end': return finish(state)
    case 'paint': {
      if (action.recipe !== 0 && !state.document.recipes.some((recipe) => recipe.index === action.recipe)) return state
      const active = state.active ?? { label: action.label, changes: new Map<number, CellChange>() }
      const changes = new Map(active.changes)
      const cells = state.document.full_stitches.cells
      const updates: CellChange[] = []
      for (const index of new Set(action.indices)) {
        if (!Number.isInteger(index) || index < 0 || index >= cells.length || state.lockedIndices.has(index) || cells[index] === action.recipe) continue
        const update = { index, before: cells[index], after: action.recipe }
        updates.push(update)
        changes.set(index, { ...update, before: changes.get(index)?.before ?? cells[index] })
      }
      if (!updates.length) return state
      const next = { ...applyChanges(state, updates, 'after'), active: { label: active.label, changes } }
      return state.active ? next : finish(next)
    }
    case 'undo': {
      const current = finish(state)
      const edit = current.past.at(-1)
      if (!edit) return current
      return { ...applyChanges(current, edit.changes, 'before'),
        past: current.past.slice(0, -1), future: [...current.future, edit] }
    }
    case 'redo': {
      const current = finish(state)
      const edit = current.future.at(-1)
      if (!edit) return current
      return { ...applyChanges(current, edit.changes, 'after'),
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
