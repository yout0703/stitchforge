import { createEditor, editorReducer, type Edit, type EditorState } from './editor'
import type { PatternDocument } from './types'

export interface Draft {
  version: 1
  userId: string
  projectId: string
  baseRevisionId: string
  document: PatternDocument
  savedDocument: PatternDocument
  past: Edit[]
  future: Edit[]
  updatedAt: number
}

// Keep whole undo operations, with a memory ceiling as well as the 100-step limit.
function boundedHistory(edits: Edit[]): Edit[] {
  let cells = 0
  const selected: Edit[] = []
  for (const edit of edits.slice(-100).reverse()) {
    if (cells + edit.changes.length > 1_000_000) break
    selected.push(edit)
    cells += edit.changes.length
  }
  return selected.reverse()
}

export function draftFromEditor(userId: string, session: { projectId: string; headRevisionId: string }, editor: EditorState): Draft {
  const finished = editorReducer(editor, { type: 'end' })
  return { version: 1, userId, projectId: session.projectId, baseRevisionId: session.headRevisionId,
    document: finished.document, savedDocument: finished.savedDocument,
    past: boundedHistory(finished.past), future: boundedHistory(finished.future), updatedAt: Date.now() }
}

export function restoreDraft(draft: Draft, serverRevisionId: string) {
  return {
    editor: { ...createEditor(draft.document, draft.savedDocument), past: draft.past, future: draft.future },
    baseRevisionId: draft.baseRevisionId,
    conflict: draft.baseRevisionId !== serverRevisionId,
  }
}

export class DraftStore {
  private async open(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open('stitchforge-drafts', 1)
      request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: ['userId', 'projectId'] })
      request.onerror = () => reject(request.error)
      request.onblocked = () => reject(new Error('草稿库被其他窗口占用'))
      request.onsuccess = () => resolve(request.result)
    })
  }

  private async transaction<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await this.open()
    try {
      return await new Promise<T>((resolve, reject) => {
        const tx = db.transaction('drafts', mode)
        const request = operation(tx.objectStore('drafts'))
        tx.oncomplete = () => resolve(request.result)
        tx.onabort = () => reject(tx.error ?? new Error('草稿保存已中断'))
        tx.onerror = () => reject(tx.error ?? new Error('草稿读写失败'))
      })
    } finally { db.close() }
  }

  async put(draft: Draft): Promise<void> {
    await this.transaction('readwrite', (store) => store.put(draft))
  }

  async get(userId: string, projectId: string): Promise<Draft | null> {
    const value = await this.transaction('readonly', (store) => store.get([userId, projectId])) as Draft | undefined
    if (!value || value.version !== 1 || value.userId !== userId || value.projectId !== projectId) return null
    if (!value.document?.full_stitches?.cells || !value.savedDocument?.full_stitches?.cells || !Array.isArray(value.past) || !Array.isArray(value.future)) {
      throw new Error('本地草稿损坏，已保留原记录')
    }
    return value
  }

  async remove(userId: string, projectId: string): Promise<void> {
    await this.transaction('readwrite', (store) => store.delete([userId, projectId]))
  }
}
