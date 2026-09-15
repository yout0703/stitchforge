import { beforeEach, expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { createEditor, editorReducer, isDirty } from './editor'
import { makeDemoPattern } from './demoPattern'
import { DraftStore, draftFromEditor, restoreDraft } from './drafts'

beforeEach(() => { globalThis.indexedDB = new IDBFactory() })

function draft(userId = 'alice', projectId = 'one') {
  let state = createEditor(makeDemoPattern())
  state = editorReducer(state, { type: 'paint', indices: [0, 1], recipe: 2, label: '画笔' })
  return draftFromEditor(userId, { projectId, headRevisionId: 'r1' }, state)
}

it('restores the exact draft and undo history after reopening storage', async () => {
  const record = draft()
  await new DraftStore().put(record)
  const loaded = await new DraftStore().get('alice', 'one')
  expect(loaded).toEqual(record)
  const restored = restoreDraft(loaded!, 'r1')
  expect(restored.conflict).toBe(false)
  expect(isDirty(restored.editor)).toBe(true)
  expect(isDirty(editorReducer(restored.editor, { type: 'undo' }))).toBe(false)
})

it('isolates users and projects and only deletes the selected draft', async () => {
  const store = new DraftStore()
  await store.put(draft())
  await store.put(draft('alice', 'two'))
  await store.put(draft('bob', 'one'))
  await store.remove('alice', 'one')
  expect(await store.get('alice', 'one')).toBeNull()
  expect(await store.get('alice', 'two')).not.toBeNull()
  expect(await store.get('bob', 'one')).not.toBeNull()
})

it('flags a newer server revision and retains the original draft base', () => {
  const restored = restoreDraft(draft(), 'r2')
  expect(restored.conflict).toBe(true)
  expect(restored.baseRevisionId).toBe('r1')
  expect(isDirty(restored.editor)).toBe(true)
})

it('rejects storage failures instead of reporting that the draft is safe', async () => {
  const factory = new IDBFactory()
  factory.open = () => { throw new DOMException('denied', 'SecurityError') }
  globalThis.indexedDB = factory
  await expect(new DraftStore().put(draft())).rejects.toThrow('denied')
})
